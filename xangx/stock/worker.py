"""Stock worker (actor `stock`): turns shelf.items_taken into stock.low_detected."""

import threading
import time
from datetime import datetime, timedelta, timezone

from xangx.shared import views
from xangx.shared.cursors import consume
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events

ACTOR = "stock"
SALES_DAYS = 14


def _at(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def greek_date(now):
    from xangx.master.tools import greek_offset

    return (now + greek_offset(now)).strftime("%Y-%m-%d")


def sales_per_day(conn, product_id, now, days=SALES_DAYS):
    """Units sold per day over the last `days` days, from sale.recorded."""
    since = _at(now - timedelta(days=days))
    sold = 0
    for event in read_events(conn, types=["sale.recorded"]):
        if event.at < since:
            continue
        for line in event.payload.get("lines", []):
            if line.get("product_id") == product_id:
                sold += line.get("qty", 0)
    return sold / days


def on_shelf_taken(conn, event, now=None):
    """Write stock.low_detected (once per Greek day) if the product is now low."""
    now = now or datetime.now(timezone.utc)
    product_id = event.payload["product_id"]
    row = conn.execute("SELECT low_threshold FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None or row["low_threshold"] is None:
        return None
    on_hand = views.stock_on_hand(conn, product_id) - event.payload["taken"]
    if on_hand > row["low_threshold"]:
        return None
    per_day = round(sales_per_day(conn, product_id, now), 1)
    days_left = round(max(on_hand, 0) / per_day, 1) if per_day > 0 else None
    written, _ = append_event(
        conn, type="stock.low_detected", actor=ACTOR, subject=f"product:{product_id}",
        caused_by=event.id, idem_key=f"low:{product_id}:{greek_date(now)}",
        payload={"product_id": product_id, "on_hand": on_hand, "per_day": per_day,
                 "days_left": days_left, "source": "camera"},
    )
    return written


EXPIRING_DAYS = 2
EXPIRY_EVERY_SECONDS = 60


def check_expiring(conn, now=None):
    """Write stock.expiring for each batch that expires within 2 days (once per batch)."""
    from xangx.stock import expiry

    written = []
    for item in expiry.expiring(conn, within_days=EXPIRING_DAYS, now=now):
        if item["days_left"] < 0:
            continue
        event, created = append_event(
            conn, type="stock.expiring", actor=ACTOR, subject=f"product:{item['product_id']}",
            idem_key=f"expiring:{item['product_id']}:{item['expires_on']}",
            payload={"product_id": item["product_id"], "qty": item["qty"],
                     "expires_on": item["expires_on"]},
        )
        if created:
            written.append(event)
    return written


def run_once(conn):
    return consume(conn, ACTOR, ["shelf.items_taken"], lambda e: on_shelf_taken(conn, e))


def _loop(db_path, interval):
    conn = connect(db_path)
    init_db(conn)
    next_expiry = 0.0
    while True:
        try:
            run_once(conn)
            if time.monotonic() >= next_expiry:
                next_expiry = time.monotonic() + EXPIRY_EVERY_SECONDS
                check_expiring(conn)
        except Exception as exc:  # keep the worker alive; the event is retried next round
            print(f"stock worker: {exc}", flush=True)
        time.sleep(interval)


def start(db_path=None, interval=1.0):
    t = threading.Thread(target=_loop, args=(db_path, interval), name="stock-worker", daemon=True)
    t.start()
    return t
