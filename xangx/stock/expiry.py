"""What expires soon: batches rebuilt from the stock.moved events, oldest sold first.

Every positive move (delivery, invoice, count, seed) is a batch that entered the
store on that Greek day. Every negative move (sale, waste) takes from the oldest
batch first. The shelf life per product is fixed (products.shelf_life_days); a
product without a shelf life (NULL) never expires.
"""

import json
from collections import deque
from datetime import date, datetime, timedelta, timezone

from xangx.master.tools import greek_offset


def _greek_day(at):
    """The Greek calendar day of a UTC event time like 2026-11-14T08:30:00.000Z."""
    utc = datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (utc + greek_offset(utc)).date()


def today_greek(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + greek_offset(now)).date()


def batches(conn, product_ids=None, until_id=None):
    """Batches left in stock: {product_id: [(entered_on, qty), ...]}, oldest first."""
    shelf = {
        r["id"]: r["shelf_life_days"]
        for r in conn.execute("SELECT id, shelf_life_days FROM products WHERE shelf_life_days IS NOT NULL")
    }
    if product_ids is not None:
        shelf = {k: v for k, v in shelf.items() if k in set(product_ids)}
    queues = {pid: deque() for pid in shelf}
    sql = "SELECT at, payload FROM events WHERE type = 'stock.moved'"
    params = []
    if until_id is not None:
        sql += " AND id <= ?"
        params.append(until_id)
    rows = conn.execute(sql + " ORDER BY id", params)
    for row in rows:
        payload = json.loads(row["payload"])
        pid = payload.get("product_id")
        if pid not in queues:
            continue
        qty = payload.get("qty") or 0
        queue = queues[pid]
        if qty > 0:
            queue.append([_greek_day(row["at"]), qty])
        else:
            need = -qty
            while need > 0 and queue:
                if queue[0][1] <= need:
                    need -= queue.popleft()[1]
                else:
                    queue[0][1] -= need
                    need = 0
    return {pid: [(d, q) for d, q in queue] for pid, queue in queues.items() if queue}


def expiring(conn, within_days=3, now=None, until_id=None):
    """Batches with stock left that expire within `within_days` days or already expired.

    Each item: product_id, name, qty, expires_on (YYYY-MM-DD), days_left (0 = today,
    negative = already expired). Sorted by date, then name.
    """
    today = today_greek(now)
    info = {
        r["id"]: (r["name"], r["shelf_life_days"])
        for r in conn.execute("SELECT id, name, shelf_life_days FROM products WHERE shelf_life_days IS NOT NULL")
    }
    out = []
    for pid, items in batches(conn, until_id=until_id).items():
        name, life = info[pid]
        for entered, qty in items:
            expires = entered + timedelta(days=life)
            days_left = (expires - today).days
            if days_left <= within_days:
                out.append({
                    "product_id": pid, "name": name, "qty": qty,
                    "expires_on": expires.isoformat(), "days_left": days_left,
                })
    out.sort(key=lambda x: (x["expires_on"], x["name"]))
    return out


def when_text(days_left):
    """Greek label for a number of days left, e.g. «Λήγουν αύριο»."""
    if days_left < 0:
        return "Έχουν λήξει"
    if days_left == 0:
        return "Λήγουν σήμερα"
    if days_left == 1:
        return "Λήγουν αύριο"
    return f"Σε {days_left} μέρες"


def summary_text(items, within_days):
    """One Greek sentence: «Λήγουν αύριο: 6 Γάλα 1L· σε 2 μέρες: 4 Γιαούρτι»."""
    if not items:
        return f"Δεν λήγει τίποτα τις επόμενες {within_days} μέρες."
    groups = {}
    for item in items:
        groups.setdefault(item["days_left"], []).append(item)
    parts = []
    for days_left in sorted(groups):
        label = when_text(days_left)
        if parts:
            label = label[0].lower() + label[1:]
        what = ", ".join(f"{_num(i['qty'])} {i['name']}" for i in groups[days_left])
        parts.append(f"{label}: {what}")
    return "· ".join(parts)


def _num(qty):
    return str(int(qty)) if float(qty) == int(qty) else str(qty).replace(".", ",")
