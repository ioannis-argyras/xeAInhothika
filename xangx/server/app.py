"""The web app: state for the home screen, live event stream, chat and approvals."""

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from xangx.screen.describe import describe, load_names
from xangx.shared import views
from xangx.shared.db import connect, init_db
from xangx.shared.events import EventError, append_event, read_events

STATIC_DIR = Path(__file__).resolve().parent.parent / "screen" / "static"

app = FastAPI(title="Xangxothaika")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

NO_MASTER = "Ο συνεργάτης δεν είναι ακόμα διαθέσιμος. Δοκίμασε ξανά σε λίγο."


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

    return {
        "pending_approvals": approvals,
        "shelves": shelf_list,
        "stock": low,
        "today": {"sales_cents": sales["s"], "sales_count": sales["n"]},
        "recent": recent,
        "paused": views.is_paused(conn, until_id=until_id),
        "last_id": last_id,
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


def _pause_sync(kind):
    conn = _db()
    try:
        event, _ = append_event(conn, type=kind, actor="owner", payload={})
        return {"ok": True, "event_id": event.id, "paused": kind == "system.paused"}
    except EventError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


@app.post("/api/pause")
async def api_pause():
    return await run_in_threadpool(_pause_sync, "system.paused")


@app.post("/api/resume")
async def api_resume():
    return await run_in_threadpool(_pause_sync, "system.resumed")
