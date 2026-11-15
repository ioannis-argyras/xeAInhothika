"""Tool catalog for the master agent.

Every tool is a function `fn(conn, **args) -> dict`, registered with @tool.
Stavros and Manolis add their own tools in `xangx/<role>/tools.py` with the same
decorator; `load_role_tools()` imports those modules so the tools get registered.

A tool with needs_approval=True is never run by the loop. The loop writes an
`approval.requested` event instead, and the tool runs only after the owner says yes.
"""

import copy
import importlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from xangx.shared import append_event, read_events
from xangx.shared import views
from xangx.shared.money import format_eur

ROLE_TOOL_MODULES = (
    "xangx.stock.tools",
    "xangx.orders.tools",
    "xangx.invoices.tools",
    "xangx.accountant.tools",
)

MAX_LIST_EVENTS = 50


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable
    needs_approval: bool = False
    approval_action: str | None = None
    # Optional hook: describe(args) -> dict with any of summary, exact, why, ref.
    describe: Callable | None = field(default=None, repr=False)


TOOLS: dict[str, Tool] = {}


def tool(
    name,
    description,
    input_schema,
    needs_approval=False,
    approval_action=None,
    describe=None,
):
    """Register a tool. The decorated function is `fn(conn, **args) -> dict`.

    Put "summary" (a short Greek sentence) and "event_ids" (list) in the returned
    dict to fill the agent.tool_ran event.
    """

    def register(fn):
        TOOLS[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            fn=fn,
            needs_approval=needs_approval,
            approval_action=approval_action,
            describe=describe,
        )
        return fn

    return register


def tool_definitions():
    """The tools in the shape the API wants, sorted by name (stable for caching)."""
    definitions = []
    for name in sorted(TOOLS):
        t = TOOLS[name]
        schema = copy.deepcopy(t.input_schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        definitions.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": schema,
                "strict": True,
            }
        )
    return definitions


def load_role_tools():
    """Import the role tool modules that exist, so they register their tools."""
    for module in ROLE_TOOL_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            # Only a missing role module is fine; a broken import inside one is not.
            if exc.name and module.startswith(exc.name):
                continue
            raise


# --- Greek time -----------------------------------------------------------

def _last_sunday(year, month):
    day = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def greek_offset(at_utc):
    """UTC offset of Greece at the given UTC time (DST: last Sunday of March to October)."""
    year = at_utc.year
    start = _last_sunday(year, 3).replace(hour=1, tzinfo=timezone.utc)
    end = _last_sunday(year, 10).replace(hour=1, tzinfo=timezone.utc)
    return timedelta(hours=3) if start <= at_utc < end else timedelta(hours=2)


def greek_day_start_utc(now=None):
    """Start of the current Greek day, as a UTC ISO string like the `at` column."""
    now = now or datetime.now(timezone.utc)
    local = now + greek_offset(now)
    midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight_local - greek_offset(midnight_local - greek_offset(now))
    return start.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def greek_time(at_utc=None):
    """'14/11/2026 17:10' in Greek time, from an `at` string (or now). The agent never sees UTC."""
    if at_utc is None:
        moment = datetime.now(timezone.utc)
    else:
        moment = datetime.strptime(at_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (moment + greek_offset(moment)).strftime("%d/%m/%Y %H:%M")


# --- Shared readings of the store ------------------------------------------

def _stock_levels(conn):
    fn = getattr(views, "stock_levels", None)
    if fn is not None:
        return fn(conn)
    rows = conn.execute(
        "SELECT json_extract(payload, '$.product_id') AS pid, "
        "SUM(json_extract(payload, '$.qty')) AS qty "
        "FROM events WHERE type = 'stock.moved' GROUP BY pid"
    ).fetchall()
    return {r["pid"]: r["qty"] for r in rows}


def low_products(conn):
    """Products whose on-hand stock is at or below their low_threshold, or that the
    camera flagged today (stock.low_detected, with its newer on_hand)."""
    levels = _stock_levels(conn)
    since = greek_day_start_utc()
    flagged = {}
    for e in read_events(conn, types=["stock.low_detected"]):
        if e.at >= since and e.payload.get("product_id") is not None:
            flagged[e.payload["product_id"]] = e.payload.get("on_hand")
    rows = conn.execute(
        "SELECT id, name, low_threshold FROM products "
        "WHERE low_threshold IS NOT NULL ORDER BY name"
    ).fetchall()
    low = []
    seen = set()
    for r in rows:
        on_hand = levels.get(r["id"], 0) or 0
        if r["id"] in flagged and flagged[r["id"]] is not None:
            on_hand = flagged[r["id"]]
        elif on_hand > r["low_threshold"]:
            continue
        seen.add(r["id"])
        low.append(
            {
                "product_id": r["id"],
                "name": r["name"],
                "on_hand": on_hand,
                "low_threshold": r["low_threshold"],
            }
        )
    extra = conn.execute(
        "SELECT id, name, low_threshold FROM products WHERE low_threshold IS NULL ORDER BY name"
    ).fetchall()
    for r in extra:
        if r["id"] in flagged and r["id"] not in seen and flagged[r["id"]] is not None:
            low.append(
                {
                    "product_id": r["id"],
                    "name": r["name"],
                    "on_hand": flagged[r["id"]],
                    "low_threshold": None,
                }
            )
    return low


def low_suppliers(conn, low):
    """Suppliers with the ids of their products that are currently low."""
    low_ids = {p["product_id"] for p in low}
    out = []
    for s in conn.execute("SELECT id, name FROM suppliers ORDER BY name").fetchall():
        ids = [
            r["product_id"]
            for r in conn.execute(
                "SELECT product_id FROM supplier_products WHERE supplier_id = ? "
                "ORDER BY product_id",
                (s["id"],),
            )
            if r["product_id"] in low_ids
        ]
        out.append({"id": s["id"], "name": s["name"], "low_product_ids": ids})
    return out


def sales_today(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(payload, '$.total_cents')), 0) AS total "
        "FROM events WHERE type = 'sale.recorded' AND at >= ?",
        (greek_day_start_utc(),),
    ).fetchone()
    return {"sales_count": row["n"], "sales_cents": int(row["total"])}


_WEEKDAYS_PLURAL = [
    "Δευτέρες", "Τρίτες", "Τετάρτες", "Πέμπτες", "Παρασκευές", "Σάββατα", "Κυριακές",
]
_USUAL_WEEKS = 4
_MIN_HISTORY_WEEKS = 2


def _sales_between(conn, start, end):
    row = conn.execute(
        "SELECT COALESCE(SUM(json_extract(payload, '$.total_cents')), 0) AS total "
        "FROM events WHERE type = 'sale.recorded' AND at >= ? AND at < ?",
        (start, end),
    ).fetchone()
    return int(row["total"])


def _parse_day_start(day_start):
    return datetime.strptime(day_start[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def sales_vs_usual(conn, now=None):
    """Yesterday's sales (Greek day) against the average of the same weekday in the
    4 weeks before. Needs at least 2 of those weeks with sales, otherwise no comparison."""
    now = now or datetime.now(timezone.utc)
    today_start = _parse_day_start(greek_day_start_utc(now))
    yesterday_start = greek_day_start_utc(today_start - timedelta(hours=1))
    yesterday_cents = _sales_between(conn, yesterday_start, greek_day_start_utc(now))
    y_dt = _parse_day_start(yesterday_start)
    weekday = (y_dt + greek_offset(y_dt) + timedelta(hours=12)).weekday()
    plural = _WEEKDAYS_PLURAL[weekday]
    weeks = []
    for k in range(1, _USUAL_WEEKS + 1):
        start = greek_day_start_utc(y_dt - timedelta(days=7 * k) + timedelta(hours=12))
        end = greek_day_start_utc(_parse_day_start(start) + timedelta(hours=36))
        total = _sales_between(conn, start, end)
        if total > 0:
            weeks.append(total)
    out = {"yesterday_cents": yesterday_cents, "weekday": plural}
    if len(weeks) < _MIN_HISTORY_WEEKS:
        out.update(
            state="no_history", pct=None, usual_cents=None,
            text="Δεν υπάρχει ακόμα αρκετό ιστορικό για σύγκριση",
        )
        return out
    usual = sum(weeks) / len(weeks)
    pct = round((yesterday_cents - usual) / usual * 100)
    amount = format_eur(yesterday_cents)
    if abs(yesterday_cents - usual) / usual * 100 < 2:
        state = "same"
        text = f"Χθες πούλησες {amount}, όπως συνήθως τα {plural}"
    elif yesterday_cents > usual:
        state = "up"
        text = f"Χθες πούλησες {amount}, {abs(pct)}% πάνω από τα συνηθισμένα {plural}"
    else:
        state = "down"
        text = f"Χθες πούλησες {amount}, {abs(pct)}% κάτω από τα συνηθισμένα {plural}"
    out.update(state=state, pct=pct, usual_cents=int(round(usual)), text=text)
    return out


def store_status(conn):
    pending = views.pending_approvals(conn)
    return {
        "pending_approvals": [
            {
                "approval_id": e.payload.get("approval_id"),
                "summary": e.payload.get("summary"),
            }
            for e in pending
        ],
        "low_products": low_products(conn),
        "sales_today": sales_today(conn),
        "sales_vs_usual": sales_vs_usual(conn),
        "paused": views.is_paused(conn),
    }


# --- Master tools -------------------------------------------------------------

@tool(
    "get_store_status",
    "Get the current state of the store: approvals waiting for the owner, products "
    "that are low on stock, today's sales, and whether the system is paused.",
    {"type": "object", "properties": {}, "required": []},
)
def get_store_status(conn):
    status = store_status(conn)
    status["suppliers"] = low_suppliers(conn, status["low_products"])
    status["now_greece"] = greek_time()
    status["summary"] = (
        f"Κατάσταση: {len(status['pending_approvals'])} εγκρίσεις σε αναμονή, "
        f"{len(status['low_products'])} προϊόντα σε χαμηλό"
    )
    return status


def _short_event(event):
    item = {
        "id": event.id,
        "time_greece": greek_time(event.at),
        "actor": event.actor,
        "type": event.type,
        "subject": event.subject,
    }
    text = event.payload.get("text") or event.payload.get("summary")
    if isinstance(text, str):
        item["text"] = text[:120]
    return item


@tool(
    "list_recent_events",
    "List the most recent events of the store in short form (newest last). "
    "Optionally only some event types.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many events, at most 50."},
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only these event types, e.g. sale.recorded.",
            },
        },
        "required": [],
    },
)
def list_recent_events(conn, limit=20, types=None):
    limit = max(1, min(int(limit), MAX_LIST_EVENTS))
    sql = "SELECT id FROM events"
    params = []
    if types:
        sql += " WHERE type IN (" + ",".join("?" * len(types)) + ")"
        params.extend(types)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    ids = [r["id"] for r in conn.execute(sql, params).fetchall()]
    if not ids:
        return {"events": [], "summary": "Δεν υπάρχουν γεγονότα"}
    events = read_events(conn, after_id=min(ids) - 1, types=types)
    events = events[-limit:]
    return {
        "events": [_short_event(e) for e in events],
        "summary": f"Διάβασα {len(events)} πρόσφατα γεγονότα",
    }


@tool(
    "notify_owner",
    "Send a short message to the owner (shown in the app and sent to Telegram).",
    {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The message, in Greek."}},
        "required": ["text"],
    },
)
def notify_owner(conn, text):
    event, _ = append_event(
        conn,
        type="notification.sent",
        actor="master",
        payload={"channel": "all", "text": str(text)[:2000]},
    )
    return {"sent": True, "event_ids": [event.id], "summary": "Έστειλα ειδοποίηση στον ιδιοκτήτη"}
