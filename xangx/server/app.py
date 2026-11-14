"""The web app: state for the home screen, live event stream, chat and approvals."""

import asyncio
import importlib
import json
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from xangx.screen.api_b1 import router as api_b1_router
from xangx.screen.api_b2 import router as api_b2_router
from xangx.screen.api_c import router as api_c_router
from xangx.screen.describe import _mask, describe, load_names
from xangx.server.access import access_middleware
from xangx.server.mcp import router as mcp_router
from xangx.shared import payments, undo, views
from xangx.shared.catalog import CATALOG
from xangx.shared.db import connect, init_db
from xangx.shared.events import EventError, append_event, read_events

# Windows has no font types in its registry: without this the .woff2 files go out as octet-stream.
mimetypes.add_type("font/woff2", ".woff2")

STATIC_DIR = Path(__file__).resolve().parent.parent / "screen" / "static"

# No public API docs: nothing about the API is published through the tunnel.
app = FastAPI(title="Xangxothaika", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def always_revalidate(request: Request, call_next):
    """Screens must pick up a fix at once: the browser checks for a newer file every time."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Each role may add pages and API routes in `xangx/<role>/routes.py`, exposing
# `router = fastapi.APIRouter()`. Paths start with the role name (/stock/..., /invoices/...).
ROLE_ROUTE_MODULES = (
    "xangx.stock.routes",
    "xangx.orders.routes",
    "xangx.invoices.routes",
    "xangx.accountant.routes",
    "xangx.demo.routes",
)


def include_role_routes(target):
    """Include the routers of the role modules that exist. Returns their names."""
    included = []
    for module in ROLE_ROUTE_MODULES:
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            # Only a missing role module is fine; a broken import inside one is not.
            if exc.name and module.startswith(exc.name):
                continue
            raise
        target.include_router(mod.router)
        included.append(module)
    return included


include_role_routes(app)
app.include_router(api_b1_router)
app.include_router(api_b2_router)
app.include_router(api_c_router)
app.include_router(mcp_router)

# Added last, so it runs first: the team access code covers every route above.
app.middleware("http")(access_middleware)

NO_MASTER = "Ο συνεργάτης δεν είναι ακόμα διαθέσιμος. Δοκίμασε ξανά σε λίγο."

# What counts as "a job done on its own" on the home screen.
TASK_TYPES = {
    "check.morning_done", "stock.low_detected", "order.proposed", "order.sent",
    "order.reply_received", "delivery.completed", "invoice.read", "invoice.matched",
    "invoice.mydata_checked", "price.changed", "supplier.new_iban_seen",
    "payment.reminder_set", "credit.requested", "report.daily_created",
    "exception.flagged", "agent.woke",
}
TEAM = (
    {"key": "stock", "name": "Αποθήκη", "actors": ["camera", "stock", "scanner"]},
    {"key": "orders", "name": "Παραγγελίες", "actors": ["orders"]},
    {"key": "invoices", "name": "Τιμολόγια", "actors": ["invoices"]},
    {"key": "accountant", "name": "Λογιστής", "actors": ["accountant"]},
)
TIMELINE_ACTORS = {"camera", "stock", "scanner", "orders", "invoices", "accountant", "master"}
WEEKDAYS = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή")
ACTIVE_MINUTES = 15
TIMELINE_MAX = 60
ROUNDS_MAX = 20


def is_mcp_call(event):
    """The marker another agent's MCP call leaves behind (agent.planned with via "mcp")."""
    return event.type == "agent.planned" and (event.payload or {}).get("via") == "mcp"


def is_task(event):
    """A job done on its own: the usual kinds, the partner's own start, or an MCP request."""
    return event.type in TASK_TYPES or is_mcp_call(event)


def _db():
    """A new connection per request (sqlite3 connections are not shared between threads)."""
    conn = connect()
    init_db(conn)
    return conn


def _master():
    return importlib.import_module("xangx.master.agent")


def _last_sunday(year, month):
    day = datetime(year, month, 31, tzinfo=timezone.utc)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def athens_offset(utc_now):
    """Greek clock: UTC+3 from the last Sunday of March 01:00 UTC to the last Sunday of October 01:00 UTC, else UTC+2."""
    start = _last_sunday(utc_now.year, 3).replace(hour=1)
    end = _last_sunday(utc_now.year, 10).replace(hour=1)
    return timedelta(hours=3) if start <= utc_now < end else timedelta(hours=2)


def athens_day_start_utc(utc_now=None):
    """Start of today in Greece, as a UTC string like the `at` column."""
    utc_now = utc_now or datetime.now(timezone.utc)
    offset = athens_offset(utc_now)
    local = utc_now + offset
    midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight_local - athens_offset(midnight_local - offset)
    return start.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def is_quiet(event):
    """Till sales and their stock moves: counted in "today", not listed in the feed."""
    return event.type == "sale.recorded" or (
        event.type == "stock.moved" and event.payload.get("reason") == "sale"
    )


def event_dict(event, names):
    return {
        "quiet": is_quiet(event),
        "id": event.id,
        "at": event.at,
        "actor": event.actor,
        "type": event.type,
        "text": describe(event, names),
    }


def _stock_levels(conn, until_id):
    levels = getattr(views, "stock_levels", None)
    if levels is not None:
        return levels(conn, until_id=until_id)
    sql = ("SELECT json_extract(payload,'$.product_id') AS pid, "
           "SUM(json_extract(payload,'$.qty')) AS qty FROM events "
           "WHERE type='stock.moved'" + (" AND id <= ?" if until_id is not None else "")
           + " GROUP BY pid")
    rows = conn.execute(sql, (until_id,) if until_id is not None else ()).fetchall()
    return {r["pid"]: r["qty"] for r in rows}


def _last_id(conn, until_id=None):
    if until_id is None:
        row = conn.execute("SELECT COALESCE(MAX(id),0) AS m FROM events").fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(id),0) AS m FROM events WHERE id <= ?", (until_id,)
        ).fetchone()
    return row["m"]


_LINE = re.compile(r"^(?:(\d+(?:[.,]\d+)?)\s+)?(.+?)\s+×\s+[^×]+$")


def exact_lines(exact):
    """The lines of an order's `exact` text as {qty, text}, without the price. [] if it is not that shape."""
    if not exact:
        return []
    lines = []
    for raw in str(exact).split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        m = _LINE.match(raw)
        if not m:
            return []
        q = m.group(1)
        qty = None if q is None else (int(q) if q.isdigit() else float(q.replace(",", ".")))
        lines.append({"qty": qty, "text": m.group(2)})
    return lines


def _parse_utc(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _local(utc_dt):
    return utc_dt + athens_offset(utc_dt)


def _reference_time(conn, until_id):
    """"Now" for the home screen: the real clock, or the last event up to `until_id`."""
    if until_id is not None:
        row = conn.execute(
            "SELECT at FROM events WHERE id <= ? ORDER BY id DESC LIMIT 1", (until_id,)
        ).fetchone()
        if row:
            return _parse_utc(row["at"])
    return datetime.now(timezone.utc)


def _today_events(conn, day_start, until_id):
    """Today's events except till sales, oldest first."""
    row = conn.execute("SELECT MIN(id) AS m FROM events WHERE at >= ?", (day_start,)).fetchone()
    if row["m"] is None:
        return []
    types = [t for t in CATALOG if t != "sale.recorded"]
    return [e for e in read_events(conn, after_id=row["m"] - 1, until_id=until_id, types=types)
            if e.at >= day_start]


def _minute_of_day(at):
    local = _local(_parse_utc(at))
    return local.hour * 60 + local.minute


def _home_sections(conn, until_id, names, low):
    """Day, tasks, team, timeline and shelf focus for the home screen."""
    now_utc = _reference_time(conn, until_id)
    day_start = athens_day_start_utc(now_utc)
    events = _today_events(conn, day_start, until_id)
    local = _local(now_utc)

    morning = next((e for e in events if e.type == "check.morning_done"), None)
    day = {
        "date": local.strftime("%d/%m/%Y"),
        "weekday": WEEKDAYS[local.weekday()],
        "now": local.strftime("%H:%M"),
        "morning_done_at": (
            _local(_parse_utc(morning.at)).strftime("%H:%M") if morning else None),
    }

    tasks = {
        "done": sum(1 for e in events if is_task(e)),
        "with_yes": sum(1 for e in events
                        if e.type == "approval.decided" and e.payload.get("decision") == "yes"),
        "errors": sum(1 for e in events
                      if e.type == "agent.tool_ran" and not e.payload.get("ok")),
    }

    team = []
    team_of = {}
    for t in TEAM:
        for actor in t["actors"]:
            team_of[actor] = t["key"]
        last = None
        for e in events:
            if e.actor in t["actors"] and not is_quiet(e):
                last = e
        active = bool(last) and (
            now_utc - _parse_utc(last.at) <= timedelta(minutes=ACTIVE_MINUTES))
        team.append({"key": t["key"], "name": t["name"], "actors": list(t["actors"]),
                     "last": event_dict(last, names) if last else None, "active": active})

    undo_index = undo.Index(conn, until_id)
    timeline = [
        {"id": e.id, "at": e.at, "minute": _minute_of_day(e.at),
         "team": "agent" if is_mcp_call(e) else team_of.get(e.actor, "master"),
         "text": describe(e, names), "undo": undo_index.state(e, now_utc)}
        for e in events
        if e.actor in TIMELINE_ACTORS and not is_quiet(e)
        and (is_task(e) or e.type == "approval.requested")
    ][-TIMELINE_MAX:]

    shelf_focus = None
    for item in low:
        taken = [e for e in events
                 if e.type == "shelf.items_taken" and e.payload.get("product_id") == item["product_id"]]
        if not taken:
            continue
        e = taken[-1]
        t = e.payload.get("taken") or 0
        pos = e.payload.get("position_id")
        shelf_focus = {
            "position_name": names["position"].get(pos) or pos,
            "product_name": item["name"],
            "at": _local(_parse_utc(e.at)).strftime("%H:%M"),
            "taken": t,
            "left": item["on_hand"],
            "before": item["on_hand"] + t,
        }
        break
    return day, tasks, team, timeline, shelf_focus


def _rounds(conn, until_id, names):
    """Today's rounds the partner did not start for the owner: the clock's (agent.woke) and
    another agent's MCP calls, each with its answer and steps, oldest first."""
    day_start = athens_day_start_utc(_reference_time(conn, until_id))
    events = _today_events(conn, day_start, until_id)
    pending = {e.payload.get("approval_id") for e in views.pending_approvals(conn, until_id=until_id)}
    by_cause = {}
    for e in events:
        if e.caused_by is not None:
            by_cause.setdefault(e.caused_by, []).append(e)
    rounds = []
    for e in events:
        if e.type == "agent.woke":
            kind, label = "clock", "Μόνος του · " + str(e.payload.get("reason") or "")
        elif is_mcp_call(e):
            kind, label = "mcp", "Άλλος agent μέσω MCP"
        else:
            continue
        text, steps = None, []
        if kind == "mcp":
            first = (e.payload.get("steps") or [{}])[0]
            text = "Ζήτησε: " + str(first.get("summary") or first.get("tool") or "εργαλείο")
        for x in by_cause.get(e.id, []):
            if x.type == "agent.said":
                text = x.payload.get("text")
            elif x.type == "agent.tool_ran":
                steps.append({"text": _mask(str(x.payload.get("summary") or "")), "waiting": False})
            elif x.type == "approval.requested":
                steps.append({"text": _mask(str(x.payload.get("summary") or "")),
                              "waiting": x.payload.get("approval_id") in pending})
        rounds.append({
            "id": e.id, "at": e.at, "kind": kind, "label": _mask(label),
            "hhmm": _local(_parse_utc(e.at)).strftime("%H:%M"),
            "text": _mask(str(text)) if text else None, "steps": steps,
        })
    return rounds[-ROUNDS_MAX:]


def build_state(conn, until_id=None):
    names = load_names(conn)
    last_id = _last_id(conn, until_id)

    approvals = []
    for e in views.pending_approvals(conn, until_id=until_id):
        p = e.payload
        approvals.append({
            "approval_id": p.get("approval_id"),
            "summary": p.get("summary"),
            "exact": p.get("exact"),
            "why": p.get("why"),
            "action": p.get("action"),
            "lines": exact_lines(p.get("exact")),
        })

    shelves = {}
    for e in read_events(conn, until_id=until_id,
                         types=["shelf.items_taken", "shelf.items_added"]):
        pos = e.payload.get("position_id")
        entry = shelves.setdefault(pos, {
            "position_id": pos,
            "position_name": names["position"].get(pos) or pos,
            "product_id": e.payload.get("product_id"),
            "product_name": names["product"].get(e.payload.get("product_id"))
            or e.payload.get("product_id"),
            "last_taken": None,
            "last_added": None,
            "last_id": 0,
        })
        key = "last_taken" if e.type == "shelf.items_taken" else "last_added"
        entry[key] = {"at": e.at, "qty": e.payload.get("taken", e.payload.get("added"))}
        entry["last_id"] = e.id
    shelf_list = sorted(shelves.values(), key=lambda s: -s["last_id"])[:10]

    low = []
    for product_id, on_hand in _stock_levels(conn, until_id).items():
        row = conn.execute(
            "SELECT name, low_threshold FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None or row["low_threshold"] is None:
            continue
        if on_hand <= row["low_threshold"]:
            low.append({"product_id": product_id, "name": row["name"],
                        "on_hand": on_hand, "low": row["low_threshold"]})
    low.sort(key=lambda s: s["on_hand"] - s["low"])

    sql = ("SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(payload,'$.total_cents')),0) AS s "
           "FROM events WHERE type='sale.recorded' AND at >= ?")
    params = [athens_day_start_utc()]
    if until_id is not None:
        sql += " AND id <= ?"
        params.append(until_id)
    sales = conn.execute(sql, params).fetchone()

    after = max(0, last_id - 1500)
    recent_events = [
        e for e in read_events(conn, after_id=after, until_id=until_id) if not is_quiet(e)
    ][-30:]
    recent = [event_dict(e, names) for e in reversed(recent_events)]

    day, tasks, team, timeline, shelf_focus = _home_sections(conn, until_id, names, low)

    ref = _reference_time(conn, until_id)
    pending_payments = []
    for item in payments.open_payments(
            conn, athens_day_start_utc(ref), _local(ref).strftime("%Y-%m-%d"), until_id=until_id):
        title, line = payments.card_texts(item, names)
        pending_payments.append({"invoice_id": item["invoice_id"], "kind": item["kind"],
                                 "title": _mask(title), "text": _mask(line)})

    return {
        "pending_approvals": approvals,
        "pending_payments": pending_payments,
        "shelves": shelf_list,
        "stock": low,
        "today": {"sales_cents": sales["s"], "sales_count": sales["n"]},
        "recent": recent,
        "paused": views.is_paused(conn, until_id=until_id),
        "last_id": last_id,
        "day": day,
        "tasks": tasks,
        "team": team,
        "timeline": timeline,
        "shelf_focus": shelf_focus,
        "rounds": _rounds(conn, until_id, names),
    }


def new_events(after_id):
    """Events after `after_id` as dicts, using a short-lived connection."""
    conn = _db()
    try:
        names = load_names(conn)
        return [event_dict(e, names) for e in read_events(conn, after_id=after_id)]
    finally:
        conn.close()


def _state_sync(until_id):
    conn = _db()
    try:
        return build_state(conn, until_id)
    finally:
        conn.close()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def api_state(until_id: int | None = None):
    return await run_in_threadpool(_state_sync, until_id)


@app.get("/api/events/stream")
async def api_stream(request: Request, after_id: int | None = None):
    def start_id():
        conn = _db()
        try:
            return _last_id(conn)
        finally:
            conn.close()

    cursor = after_id if after_id is not None else await run_in_threadpool(start_id)

    async def gen():
        nonlocal cursor
        idle = 0
        yield ": ping\n\n"
        while True:
            if await request.is_disconnected():
                return
            for item in await run_in_threadpool(new_events, cursor):
                cursor = item["id"]
                idle = 0
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(1)
            idle += 1
            if idle >= 15:
                idle = 0
                yield ": ping\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _chat_sync(text):
    master = _master()
    conn = _db()
    try:
        return master.handle_owner_message(conn, text, "web")
    finally:
        conn.close()


def _approval_sync(approval_id, decision):
    master = _master()
    conn = _db()
    try:
        return master.decide_approval(conn, approval_id, decision, "web")
    finally:
        conn.close()


@app.post("/api/chat")
async def api_chat(body: dict = Body(...)):
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Γράψε πρώτα κάτι.")
    try:
        return await run_in_threadpool(_chat_sync, text)
    except ImportError:
        return JSONResponse({"detail": NO_MASTER}, status_code=503)


@app.post("/api/approvals/{approval_id}")
async def api_approval(approval_id: str, body: dict = Body(...)):
    decision = body.get("decision")
    if decision not in ("yes", "no"):
        raise HTTPException(400, "Η απόφαση είναι «yes» ή «no».")
    try:
        return await run_in_threadpool(_approval_sync, approval_id, decision)
    except ImportError:
        return JSONResponse({"detail": NO_MASTER}, status_code=503)


def _payment_sync(invoice_id, method):
    conn = _db()
    try:
        today = _local(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        event = payments.record_payment(conn, invoice_id, method, today)
        if event is None:
            raise HTTPException(404, "Δεν έχω ρωτήσει για αυτό το τιμολόγιο.")
        return {"ok": True, "event_id": event.id}
    except EventError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


@app.post("/api/payments/{invoice_id}")
async def api_payment(invoice_id: str, body: dict = Body(...)):
    method = body.get("method")
    if method not in payments.METHODS:
        raise HTTPException(400, "Η απάντηση είναι cash, card, bank ή not_yet.")
    return await run_in_threadpool(_payment_sync, invoice_id, method)


def _pause_sync(kind):
    conn = _db()
    try:
        event, _ = append_event(conn, type=kind, actor="owner", payload={})
        return {"ok": True, "event_id": event.id, "paused": kind == "system.paused"}
    except EventError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def _undo_sync(event_id):
    conn = _db()
    try:
        return undo.undo(conn, event_id)
    except EventError as exc:
        return 409, {"detail": str(exc)}
    finally:
        conn.close()


@app.post("/api/undo/{event_id}")
async def api_undo(event_id: int):
    """Take back one thing the owner may take back, written as the opposite event."""
    status, body = await run_in_threadpool(_undo_sync, event_id)
    if status != 200:
        return JSONResponse(body, status_code=status)
    return body


@app.post("/api/pause")
async def api_pause():
    return await run_in_threadpool(_pause_sync, "system.paused")


@app.post("/api/resume")
async def api_resume():
    return await run_in_threadpool(_pause_sync, "system.resumed")
