"""Sales trends for the owner, read-only, from sale.recorded (the till) only.

Days are Greek days (Europe/Athens, with summer time), counted back from "now": the real
clock, or the last event up to `until_id` (like /api/state). Amounts in cents.
"""

from datetime import datetime, timedelta, timezone

from xangx.master.tools import greek_offset
from xangx.shared.events import read_events

MAX_DAYS = 365
TOP = 10


def _utc(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _local(moment):
    return moment + greek_offset(moment)


def _now(conn, until_id):
    if until_id is not None:
        row = conn.execute("SELECT at FROM events WHERE id <= ? ORDER BY id DESC LIMIT 1",
                           (until_id,)).fetchone()
        if row:
            return _utc(row["at"])
    return datetime.now(timezone.utc)


def _ddmmyyyy(day):
    return day.strftime("%d/%m/%Y")


def sales_trends(conn, until_id=None, days=30):
    """{"from", "to", "days", "until_id", "per_day", "per_hour", "top_products"}.

    per_day: every Greek day of the window, oldest first, zeros included.
    per_hour: 24 hours of the Greek day, the average over the window's days.
    top_products: the TOP products by units sold, then by amount.
    """
    days = max(1, min(MAX_DAYS, int(days)))
    now = _now(conn, until_id)
    last_day = _local(now).date()
    first_day = last_day - timedelta(days=days - 1)
    # One day of margin either side of the UTC offset; the Greek date decides below.
    cutoff = (datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)
              - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    row = conn.execute("SELECT MIN(id) AS m FROM events WHERE at >= ?", (cutoff,)).fetchone()
    sales = [] if row["m"] is None else read_events(
        conn, after_id=row["m"] - 1, until_id=until_id, types=["sale.recorded"])

    per_day = {first_day + timedelta(days=i): [0, 0] for i in range(days)}
    per_hour = [[0, 0] for _ in range(24)]
    products = {}
    for e in sales:
        local = _local(_utc(e.at))
        if not first_day <= local.date() <= last_day:
            continue
        total = int(e.payload.get("total_cents") or 0)
        per_day[local.date()][0] += total
        per_day[local.date()][1] += 1
        per_hour[local.hour][0] += total
        per_hour[local.hour][1] += 1
        for line in e.payload.get("lines") or []:
            p = products.setdefault(line.get("product_id"), [0, 0])
            p[0] += line.get("qty") or 0
            p[1] += int(line.get("gross_cents") or 0)

    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products")}
    top = sorted(((pid, q, c) for pid, (q, c) in products.items() if pid),
                 key=lambda t: (-t[1], -t[2], t[0]))[:TOP]
    last_id = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events"
                           + (" WHERE id <= ?" if until_id is not None else ""),
                           (until_id,) if until_id is not None else ()).fetchone()["m"]
    return {
        "from": _ddmmyyyy(first_day), "to": _ddmmyyyy(last_day), "days": days, "until_id": last_id,
        "per_day": [{"date": _ddmmyyyy(d), "total_cents": t, "receipts": n}
                    for d, (t, n) in sorted(per_day.items())],
        "per_hour": [{"hour": h, "avg_total_cents": round(t / days), "avg_receipts": round(n / days, 1)}
                     for h, (t, n) in enumerate(per_hour)],
        "top_products": [{"product_id": pid, "name": names.get(pid, pid), "qty": q, "total_cents": c}
                         for pid, q, c in top],
    }
