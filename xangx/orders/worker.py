"""Orders worker: every 30 s, send orders that waited for a connection, compare prices, and
read supplier replies from the mailbox (IMAP_* in .env).

Without IMAP_HOST the mailbox part is skipped: rehearsals run without a mailbox.
"""

import threading
import time

from xangx.orders import replies, tools
from xangx.shared.cursors import consume
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event

INTERVAL = 30.0


def cheaper_for(conn, event):
    """After a price rise: supplier.cheaper_found if another supplier has the same product cheaper."""
    p = event.payload
    if p["pct"] <= 0:
        return None
    others = [x for x in tools.supplier_prices(conn, p["product_id"])
              if x[0] != p["supplier_id"] and x[2] < p["new_cents"]]
    if not others:
        return None
    to_id, _, to_cents = others[0]
    caused_by = _invoice_of(conn, event)
    created, _ = append_event(
        conn, type="supplier.cheaper_found", actor=tools.ACTOR,
        payload={"product_id": p["product_id"], "from_supplier_id": p["supplier_id"],
                 "from_cents": p["new_cents"], "to_supplier_id": to_id, "to_cents": to_cents,
                 "pct_change": p["pct"], "caused_by_invoice_id": caused_by},
        subject=f"product:{p['product_id']}",
        idem_key=f"cheaper:{p['product_id']}:{caused_by}", caused_by=event.id,
    )
    return created


def _invoice_of(conn, event):
    """The invoice that caused this price.changed (its caused_by is the invoice.read event)."""
    if event.caused_by:
        row = conn.execute("SELECT payload FROM events WHERE id = ?", (event.caused_by,)).fetchone()
        if row:
            import json
            return json.loads(row["payload"]).get("invoice_id") or str(event.caused_by)
    return str(event.id)


def run_once(conn):
    return consume(conn, tools.ACTOR + "-compare", ["price.changed"], lambda e: cheaper_for(conn, e))


def _loop(db_path, settings, interval):
    conn = connect(db_path)
    init_db(conn)
    while True:
        try:
            run_once(conn)
            for event in tools.retry_pending(conn):
                print(f"orders worker: {event.payload['order_id']} sent after waiting", flush=True)
            for event in (replies.poll(conn, settings) if settings else []):
                print(f"orders worker: reply to {event.payload['order_id']}", flush=True)
        except Exception as exc:  # keep polling; never print settings or email text
            print(f"orders worker: {type(exc).__name__}", flush=True)
        time.sleep(interval)


def start(db_path=None, interval=INTERVAL):
    settings = replies.imap_settings()  # None without a mailbox: only the price comparison runs
    t = threading.Thread(target=_loop, args=(db_path, settings, interval),
                         name="orders-worker", daemon=True)
    t.start()
    return t
