"""Where each agent has stopped reading, and consume()."""

from xangx.shared.events import read_events


def get_cursor(conn, actor):
    """Last event id the actor has processed (0 if none)."""
    row = conn.execute(
        "SELECT last_id FROM cursors WHERE actor = ?", (actor,)
    ).fetchone()
    return row["last_id"] if row else 0


def set_cursor(conn, actor, last_id):
    with conn:
        conn.execute(
            "INSERT INTO cursors (actor, last_id) VALUES (?, ?) "
            "ON CONFLICT(actor) DO UPDATE SET last_id = excluded.last_id",
            (actor, last_id),
        )


def consume(conn, actor, types, handler, limit=100):
    """Call handler(event) for new events of `types`, one by one.

    The cursor moves after each success. If the handler raises, the cursor
    stays at the last success and the exception propagates. Events of other
    types move the cursor without calling the handler. Returns how many
    events were handled.
    """
    wanted = set(types)
    handled = 0
    for event in read_events(conn, after_id=get_cursor(conn, actor)):
        if event.type in wanted:
            handler(event)
            handled += 1
        set_cursor(conn, actor, event.id)
        if handled >= limit:
            break
    return handled
