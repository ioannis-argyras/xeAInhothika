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
    start = midnight_local - greek_offset(now)
    return start.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
    """Products whose on-hand stock is at or below their low_threshold."""
    levels = _stock_levels(conn)
    rows = conn.execute(
        "SELECT id, name, low_threshold FROM products "
        "WHERE low_threshold IS NOT NULL ORDER BY name"
    ).fetchall()
    low = []
    for r in rows:
        on_hand = levels.get(r["id"], 0) or 0
        if on_hand <= r["low_threshold"]:
            low.append(
                {
                    "product_id": r["id"],
                    "name": r["name"],
                    "on_hand": on_hand,
                    "low_threshold": r["low_threshold"],
                }
            )
    return low


def sales_today(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(payload, '$.total_cents')), 0) AS total "
        "FROM events WHERE type = 'sale.recorded' AND at >= ?",
        (greek_day_start_utc(),),
    ).fetchone()
    return {"sales_count": row["n"], "sales_cents": int(row["total"])}


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
    status["summary"] = (
        f"Κατάσταση: {len(status['pending_approvals'])} εγκρίσεις σε αναμονή, "
        f"{len(status['low_products'])} προϊόντα σε χαμηλό"
    )
    return status


def _short_event(event):
    item = {
        "id": event.id,
        "at": event.at,
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
