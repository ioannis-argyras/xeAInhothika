"""Trends for the owner: sales per Greek day, prices per product and supplier, purchases per supplier.

Read only: it reads events and the reference tables, and writes nothing. Money is in cents.
The cheapest supplier comes from the same function as compare_suppliers (xangx.orders.tools).
"""

from datetime import date, datetime, timedelta, timezone

from xangx.master.tools import greek_offset
from xangx.shared import read_events
from xangx.shared.money import format_eur

DEFAULT_DAYS = 30
MAX_DAYS = 365
WEEKDAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]


def clamp_days(days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(MAX_DAYS, days))


def greek_date(at):
    """The Greek calendar day of an `at` string (UTC), e.g. 23:30 UTC in November -> next day."""
    moment = datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return (moment + greek_offset(moment)).date()


def today_greek(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + greek_offset(now)).date()


def _pct(new, old):
    if not old:
        return None
    return round((new - old) * 100.0 / old, 1)


# ---------- 1. sales per Greek day ----------

def sales_by_day(conn, days, now=None):
    today = today_greek(now)
    first = today - timedelta(days=days - 1)
    scan_from = first - timedelta(days=7)  # for the same weekday a week before the first day
    totals, receipts = {}, {}
    for e in read_events(conn, types=["sale.recorded"]):
        d = greek_date(e.at)
        if scan_from <= d <= today:
            totals[d] = totals.get(d, 0) + int(e.payload["total_cents"])
            receipts[d] = receipts.get(d, 0) + 1
    rows = []
    for k in range(days):
        d = first + timedelta(days=k)
        total, week_before = totals.get(d, 0), totals.get(d - timedelta(days=7), 0)
        rows.append({
            "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "today": d == today,
            "total_cents": total, "receipts": receipts.get(d, 0),
            "same_weekday_last_week_cents": week_before,
            "vs_last_week_pct": _pct(total, week_before),
        })
    # Today is not over yet: it counts in the list, not in the average or the best day.
    full = [r for r in rows if not r["today"] and r["receipts"] > 0]
    average = round(sum(r["total_cents"] for r in full) / len(full)) if full else None
    best = max(full, key=lambda r: (r["total_cents"], r["date"])) if full else None
    return {
        "days": rows,
        "total_cents": sum(r["total_cents"] for r in rows),
        "total_without_today_cents": sum(r["total_cents"] for r in rows if not r["today"]),
        "receipts": sum(r["receipts"] for r in rows),
        "average_cents": average,
        "days_with_sales": len(full),
        "best_day": {"date": best["date"], "weekday": best["weekday"],
                     "total_cents": best["total_cents"]} if best else None,
        "by_weekday": _by_weekday(full),
    }


def _by_weekday(full):
    """Per weekday (Monday first): the average of its full days with sales, and its latest day
    against that average ("τα Σάββατα +2%")."""
    out = []
    for wd, name in enumerate(WEEKDAYS):
        same = [r for r in full if date.fromisoformat(r["date"]).weekday() == wd]
        if not same:
            out.append({"weekday": name, "days": 0, "average_cents": None, "last_date": None,
                        "last_cents": None, "last_vs_average_pct": None})
            continue
        avg = round(sum(r["total_cents"] for r in same) / len(same))
        last = same[-1]
        out.append({"weekday": name, "days": len(same), "average_cents": avg,
                    "last_date": last["date"], "last_cents": last["total_cents"],
                    "last_vs_average_pct": _pct(last["total_cents"], avg)})
    return out


# ---------- 2 and 3. invoices: prices and purchases ----------

def _invoices(conn):
    """The latest invoice.read per invoice, oldest first by paper date."""
    latest = {}
    for e in read_events(conn, types=["invoice.read"]):
        latest[e.payload["invoice_id"]] = e
    return sorted(latest.values(), key=lambda e: (e.payload.get("date") or "", e.id))


def _suppliers(conn):
    return {r["afm"]: {"id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id, afm, name FROM suppliers")}


def _window(days, now):
    today = today_greek(now)
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def prices(conn, invoices=None, days=DEFAULT_DAYS, now=None):
    """Per product: every invoice price (date, supplier, unit price), the last price, its change
    against the same supplier's previous price, and the cheapest supplier (as compare_suppliers).
    saving_cents: what the last N days' quantity would have cost less at the cheapest supplier
    (net, without VAT); 0 when the last supplier is already the cheapest."""
    from xangx.orders.tools import supplier_prices

    invoices = _invoices(conn) if invoices is None else invoices
    first, last_day = _window(days, now)
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
        cheap = supplier_prices(conn, pid)
        cheapest = ({"supplier_id": cheap[0][0], "name": cheap[0][1], "unit_cents": cheap[0][2]}
                    if cheap else None)
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


def purchases(conn, days, now=None, invoices=None):
    """Per supplier: total and number of invoices with a paper date in the last N Greek days."""
    invoices = _invoices(conn) if invoices is None else invoices
    first, last_day = _window(days, now)
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


def trends(conn, days=DEFAULT_DAYS, now=None):
    days = clamp_days(days)
    first, last_day = _window(days, now)
    invoices = _invoices(conn)
    return {
        "days": days,
        "from": first,
        "to": last_day,
        "sales": sales_by_day(conn, days, now),
        "prices": prices(conn, invoices, days, now),
        "purchases": purchases(conn, days, now, invoices),
    }


def _gr(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def summary_text(data):
    s, b = data["sales"], data["purchases"]
    parts = [f"Τελευταίες {data['days']} μέρες: πωλήσεις {format_eur(s['total_cents'])}"]
    if s["average_cents"] is not None:
        parts[0] += f", μέσος όρος {format_eur(s['average_cents'])} τη μέρα"
    if s["best_day"]:
        parts.append(f"καλύτερη μέρα {_gr(s['best_day']['date'])} με "
                     f"{format_eur(s['best_day']['total_cents'])}")
    parts.append(f"αγορές {format_eur(b['total_cents'])} από {b['invoices']} τιμολόγια")
    moved = [p for p in data["prices"] if p["change_pct"]]
    if moved:
        top = max(moved, key=lambda p: abs(p["change_pct"]))
        sign = "+" if top["change_pct"] > 0 else ""
        pct = f"{sign}{top['change_pct']:.1f}".replace(".", ",")
        parts.append(f"μεγαλύτερη αλλαγή τιμής: {top['name']} {pct}%")
    return ", ".join(parts) + "."
