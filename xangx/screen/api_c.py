"""API of the history page ("Ιστορικό της μέρας"): today's events that count for the day, with their minute of the day.

The page replays the day from these events and asks the existing `/api/state?until_id=` for
"how the home screen looked at that moment". Nothing is written here.
"""

import fastapi
from starlette.concurrency import run_in_threadpool

router = fastapi.APIRouter()

# Same list as TASK_TYPES of xangx/server/app.py, plus the three kinds the replay also shows.
REPLAY_TYPES = (
    "check.morning_done", "stock.low_detected", "order.proposed", "order.sent",
    "order.reply_received", "delivery.completed", "invoice.read", "invoice.matched",
    "invoice.mydata_checked", "price.changed", "supplier.new_iban_seen",
    "payment.reminder_set", "credit.requested", "report.daily_created",
    "exception.flagged", "approval.requested", "approval.decided", "shelf.items_taken",
    "agent.woke", "agent.planned",
)
TEAM_OF_ACTOR = {
    "camera": "stock", "stock": "stock", "scanner": "stock",
    "orders": "orders", "invoices": "invoices", "accountant": "accountant",
    "master": "master", "owner": "owner",
}


def team_of(actor):
    return TEAM_OF_ACTOR.get(actor, "master")


def _wanted(event):
    """agent.planned counts only when another agent asked through MCP (via "mcp")."""
    return event.type != "agent.planned" or (event.payload or {}).get("via") == "mcp"


def replay_sync():
    # Imported here, not at the top: app.py includes this router, so a top import would be circular.
    from xangx.screen.describe import describe, load_names
    from xangx.server import app as server
    from xangx.shared.events import read_events

    conn = server._db()
    try:
        names = load_names(conn)
        day_start = server.athens_day_start_utc()
        row = conn.execute("SELECT MIN(id) AS m FROM events WHERE at >= ?", (day_start,)).fetchone()
        if row["m"] is None:
            day = server._local(server._parse_utc(day_start))
            return {"events": [], "first_id": None, "last_id": None,
                    "date": day.strftime("%d/%m/%Y"), "weekday": server.WEEKDAYS[day.weekday()]}
        events = []
        for e in read_events(conn, after_id=row["m"] - 1, types=REPLAY_TYPES):
            if e.at < day_start or server.is_quiet(e) or not _wanted(e):
                continue
            local = server._local(server._parse_utc(e.at))
            item = {
                "id": e.id,
                "minute": local.hour * 60 + local.minute,
                "hhmm": local.strftime("%H:%M"),
                "team": "agent" if e.type == "agent.planned" else team_of(e.actor),
                "type": e.type,
                "text": describe(e, names),
            }
            if e.type in ("approval.requested", "approval.decided"):
                item["approval_id"] = (e.payload or {}).get("approval_id")
            events.append(item)
        day = server._local(server._parse_utc(day_start))
        return {
            "events": events,
            "first_id": events[0]["id"] if events else None,
            "last_id": events[-1]["id"] if events else None,
            "date": day.strftime("%d/%m/%Y"),
            "weekday": server.WEEKDAYS[day.weekday()],
        }
    finally:
        conn.close()


@router.get("/api/screen/replay")
async def api_replay():
    return await run_in_threadpool(replay_sync)
