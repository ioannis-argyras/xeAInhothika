"""Purchase trends for the owner: prices per product and supplier, purchases per supplier.

Read only: it reads events and the reference tables, and writes nothing. Money is in cents.
Sales per day are the stock role's (GET /stock/trends/sales), not here.
The cheapest supplier comes from the same function as compare_suppliers (xangx.orders.tools).
`until_id` replays the past like /api/state: only events up to that id, and "today" is the
Greek day of the last of them.
"""

from datetime import datetime, timedelta, timezone

from xangx.master.tools import greek_offset
from xangx.shared import read_events

DEFAULT_DAYS = 30
MAX_DAYS = 365


def clamp_days(days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(MAX_DAYS, days))


def _parse(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def reference_now(conn, until_id=None):
    """The real clock, or the time of the last event up to `until_id` (as the time machine)."""
    if until_id is not None:
        row = conn.execute("SELECT at FROM events WHERE id <= ? ORDER BY id DESC LIMIT 1",
                           (until_id,)).fetchone()
        if row:
            return _parse(row["at"])
    return datetime.now(timezone.utc)


def today_greek(now):
    return (now + greek_offset(now)).date()


def window(days, now):
    """First and last Greek day of the last N days, as YYYY-MM-DD."""
    today = today_greek(now)
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def _pct(new, old):
    if not old:
        return None
    return round((new - old) * 100.0 / old, 1)


def _invoices(conn, until_id=None):
    """The latest invoice.read per invoice (a re-read replaces the first), oldest first by paper date."""
    latest = {}
    for e in read_events(conn, until_id=until_id, types=["invoice.read"]):
        latest[e.payload["invoice_id"]] = e
    return sorted(latest.values(), key=lambda e: (e.payload.get("date") or "", e.id))


def _suppliers(conn):
    return {r["afm"]: {"id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id, afm, name FROM suppliers")}


def _cheapest(conn, product_id, until_id):
    """As compare_suppliers. supplier_prices has no until_id yet, so in a replay of the past
    it would show today's prices: then None rather than a price from the future."""
    if until_id is not None:
        return None
    from xangx.orders.tools import supplier_prices

    cheap = supplier_prices(conn, product_id)
    return {"supplier_id": cheap[0][0], "name": cheap[0][1], "unit_cents": cheap[0][2]} if cheap else None


def prices(conn, invoices, days, now, until_id=None):
    """Per product: every invoice price (date, supplier, unit price), the last price, its change
    against the same supplier's previous price, and the cheapest supplier.
    saving_cents: what the last N days' quantity would cost less at the cheapest supplier
    (net, without VAT); 0 when the last supplier is already the cheapest."""
    first, last_day = window(days, now)
    suppliers = _suppliers(conn)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products")}
    points, qty = {}, {}
    for e in invoices:
        p = e.payload
        sup = suppliers.get(p.get("supplier_afm"), {})
        in_window = first <= (p.get("date") or "") <= last_day
        for line in p.get("lines", []):
            pid, unit = line.get("product_id"), line.get("unit_cents")
            if not pid or not unit:
                continue
            points.setdefault(pid, []).append({
                "date": p.get("date"), "supplier_id": sup.get("id"),
                "supplier_name": sup.get("name") or p.get("supplier_name"),
                "unit_cents": int(unit),
            })
            if in_window:
                qty[pid] = qty.get(pid, 0) + (line.get("qty") or 0)
    out = []
    for pid in sorted(points, key=lambda x: names.get(x, x)):
        series = points[pid]
        last = series[-1]
        before = [x for x in series[:-1] if x["supplier_id"] == last["supplier_id"]]
        previous = before[-1]["unit_cents"] if before else None
        cheapest = _cheapest(conn, pid, until_id)
        saving = 0
        if (cheapest and cheapest["supplier_id"] != last["supplier_id"]
                and cheapest["unit_cents"] < last["unit_cents"]):
            saving = round((last["unit_cents"] - cheapest["unit_cents"]) * qty.get(pid, 0))
        out.append({
            "product_id": pid, "name": names.get(pid, pid), "points": series,
            "last_cents": last["unit_cents"], "last_supplier_id": last["supplier_id"],
            "last_supplier_name": last["supplier_name"],
            "previous_cents": previous, "change_pct": _pct(last["unit_cents"], previous),
            "cheapest": cheapest, "qty_in_window": qty.get(pid, 0), "saving_cents": saving,
        })
    return out


def changes(price_rows):
    """The price changes: "ήταν / τώρα / %", biggest change first."""
    rows = [{"product_id": r["product_id"], "name": r["name"], "supplier_id": r["last_supplier_id"],
             "supplier_name": r["last_supplier_name"], "date": r["points"][-1]["date"],
             "was_cents": r["previous_cents"], "now_cents": r["last_cents"], "pct": r["change_pct"]}
            for r in price_rows if r["change_pct"]]
    return sorted(rows, key=lambda r: (-abs(r["pct"]), r["name"]))


def purchases(conn, invoices, days, now):
    """Per supplier: total and number of invoices with a paper date in the last N Greek days."""
    first, last_day = window(days, now)
    suppliers = _suppliers(conn)
    by = {}
    for e in invoices:
        p = e.payload
        if not (first <= (p.get("date") or "") <= last_day):
            continue
        afm = p.get("supplier_afm")
        sup = suppliers.get(afm, {})
        row = by.setdefault(afm, {"supplier_id": sup.get("id"),
                                  "name": sup.get("name") or p.get("supplier_name") or afm,
                                  "total_cents": 0, "invoices": 0})
        row["total_cents"] += int(p["total_cents"])
        row["invoices"] += 1
    rows = sorted(by.values(), key=lambda r: (-r["total_cents"], r["name"] or ""))
    total = sum(r["total_cents"] for r in rows)
    for r in rows:
        r["share_pct"] = round(r["total_cents"] * 100.0 / total, 1) if total else None
    return {"suppliers": rows, "total_cents": total, "invoices": sum(r["invoices"] for r in rows)}


def purchase_trends(conn, days=DEFAULT_DAYS, until_id=None, now=None):
    days = clamp_days(days)
    now = now or reference_now(conn, until_id)
    first, last_day = window(days, now)
    invoices = _invoices(conn, until_id)
    price_rows = prices(conn, invoices, days, now, until_id)
    return {
        "days": days, "from": first, "to": last_day, "until_id": until_id,
        "prices": price_rows,
        "changes": changes(price_rows),
        "purchases": purchases(conn, invoices, days, now),
    }
