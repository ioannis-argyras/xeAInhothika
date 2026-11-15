"""Data for the home screen (B): sales, stock, order, invoices, payments, undo, IBAN, last job per member.

Included by the server with `app.include_router(router)`. Route: /api/screen/home. Read only.
Everything comes from the events, with the same "today" and "now" as the other screen routes
(`until_id` is the time machine). Amounts are whole cents. A whole IBAN is never sent: only its
last four characters.
"""

import json
import re
from datetime import date, datetime

import fastapi

from xangx.accountant.core import credit_details
from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.api_b1 import (
    TASK_TYPES, _bundles, _db, _hhmm, _is_task, _label, _local, _reference, _since, _team,
    invoices_today,
)
from xangx.screen.describe import describe, load_names
from xangx.shared import undo, views
from xangx.shared.events import read_events
from xangx.stock.trends import sales_trends

router = fastapi.APIRouter()

PLURAL_DAYS = ("Δευτέρες", "Τρίτες", "Τετάρτες", "Πέμπτες", "Παρασκευές", "Σάββατα", "Κυριακές")
MEMBERS = ("master", "stock", "orders", "invoices", "accountant")
UNDO_MAX = 3
EXPIRING_MAX = 3
DOCS_MAX = 3
RECENT_MAX = 3
MONTHS = ("Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος",
          "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος")


def _first_word(name):
    return str(name or "").split(" ")[0]


def _sales(conn, until_id):
    window = sales_trends(conn, until_id=until_id, days=31)["per_day"][:-1]  # without today
    per_day = []
    for row in window:
        day = datetime.strptime(row["date"], "%d/%m/%Y")
        per_day.append({"date": row["date"], "weekday": day.weekday(),
                        "total_cents": row["total_cents"], "receipts": row["receipts"]})
    total = sum(d["total_cents"] for d in per_day)
    receipts = sum(d["receipts"] for d in per_day)
    out = {"from": per_day[0]["date"], "to": per_day[-1]["date"], "per_day": per_day,
           "total_cents": total, "receipts": receipts,
           "avg_cents": round(total / len(per_day))}
    yesterday = per_day[-1]
    prev = [d["total_cents"] for d in per_day[:-1] if d["weekday"] == yesterday["weekday"]]
    mean = sum(prev) / len(prev) if prev else 0
    pct = round((yesterday["total_cents"] - mean) / mean * 100) if len(prev) >= 2 and mean else None
    out["yesterday"] = dict(yesterday)
    out["same_day"] = {"plural": PLURAL_DAYS[yesterday["weekday"]], "prev_cents": prev, "pct": pct}
    return out


def _stock(conn, until_id, today):
    from xangx.server.app import _stock_levels  # lazy: the server imports this module

    low = []
    for product_id, on_hand in _stock_levels(conn, until_id).items():
        row = conn.execute(
            "SELECT name, low_threshold FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None or row["low_threshold"] is None or on_hand > row["low_threshold"]:
            continue
        low.append({"name": row["name"], "short": _first_word(row["name"]),
                    "on_hand": on_hand, "low": row["low_threshold"]})
    low.sort(key=lambda s: s["on_hand"] - s["low"])

    names = load_names(conn)["product"]
    nearest = {}
    for e in read_events(conn, until_id=until_id, types=["stock.expiring"]):
        expires = str(e.payload.get("expires_on") or "")[:10]
        pid = e.payload.get("product_id")
        if expires >= today.isoformat() and (pid not in nearest or expires < nearest[pid]):
            nearest[pid] = expires
    expiring = []
    for pid, expires in sorted(nearest.items(), key=lambda kv: (kv[1], str(kv[0])))[:EXPIRING_MAX]:
        name = names.get(pid) or str(pid)
        expiring.append({"name": name, "short": _first_word(name).lower(), "expires_on": expires,
                         "days": (date.fromisoformat(expires) - today).days})
    return {"low": low, "expiring": expiring}


def _order(conn, until_id, suppliers):
    events = read_events(conn, until_id=until_id, types=[
        "order.proposed", "order.sent", "order.cancelled", "delivery.started",
        "delivery.completed"])
    cancelled = {e.payload.get("order_id") for e in events if e.type == "order.cancelled"}
    proposals = [e for e in events if e.type == "order.proposed"
                 and e.payload.get("order_id") not in cancelled]
    if not proposals:
        return None
    proposed = max(proposals, key=lambda e: (e.at, e.id))  # the latest by time, not by id
    order_id = proposed.payload.get("order_id")
    step, last = "proposed", proposed
    for e in events:
        if e.type == "order.sent" and e.payload.get("order_id") == order_id:
            step, last = "sent", e
    deliveries = {e.payload.get("delivery_id") for e in events
                  if e.type == "delivery.started" and e.payload.get("order_id") == order_id}
    for e in events:
        if e.type == "delivery.completed" and e.payload.get("delivery_id") in deliveries:
            step, last = "received", e
    return {"order_id": order_id, "supplier": suppliers.get(proposed.payload.get("supplier_id")),
            "net_cents": proposed.payload.get("net_cents"), "step": step, "at": _hhmm(last.at)}


def _reminders(conn, until_id, today, now, bundles, suppliers):
    """Payment reminders not undone and not paid, the nearest due day first."""
    undone = undo.undone_ids(conn, until_id)
    paid = {e.payload.get("invoice_id")
            for e in read_events(conn, until_id=until_id, types=["payment.recorded"])
            if e.payload.get("method") != "not_yet" and e.id not in undone}
    index = undo.Index(conn, until_id)
    out = []
    for e in read_events(conn, until_id=until_id, types=["payment.reminder_set"]):
        p = e.payload
        if e.id in undone or p.get("invoice_id") in paid:
            continue
        due = str(p.get("due_on") or "")[:10]
        if not due:
            continue
        out.append({
            "event_id": e.id, "label": _label(bundles.get(p.get("invoice_id"), {}), p.get("invoice_id")),
            "supplier": suppliers.get(p.get("supplier_id")), "amount_cents": p.get("amount_cents"),
            "due_on": due, "days": (date.fromisoformat(due) - today).days,
            "can": index.state(e, now) == "can"})
    out.sort(key=lambda r: (r["due_on"], r["event_id"]))
    return out


def _iban(conn, day_start, until_id, bundles, suppliers):
    out = []
    for e in _since(conn, day_start, until_id, ["supplier.new_iban_seen"]):
        p = e.payload
        new_last4 = str(p.get("iban_last4") or "")[-4:]
        row = conn.execute("SELECT known_ibans FROM suppliers WHERE id = ?",
                           (p.get("supplier_id"),)).fetchone()
        try:
            known = json.loads(row["known_ibans"]) if row else []
        except ValueError:
            known = []
        known_last4 = None
        for iban in known:
            tail = re.sub(r"\s", "", str(iban))[-4:]
            if tail != new_last4:
                known_last4 = tail
                break
        out.append({"invoice_label": _label(bundles.get(p.get("invoice_id"), {}), p.get("invoice_id")),
                    "supplier": suppliers.get(p.get("supplier_id")),
                    "known_last4": known_last4, "new_last4": new_last4})
    return out


def _last(conn, day_start, until_id):
    """The last job of today for each team member: {"at": "HH:MM", "text": ...} or None."""
    names = load_names(conn)
    events = _since(conn, day_start, until_id, TASK_TYPES + ("agent.planned",))
    solved = views.resolved_flag_ids(conn, until_id)
    out = {key: None for key in MEMBERS}
    for e in events:
        key, _ = _team(e)
        if not _is_task(e) or key not in out or e.id in solved:
            continue
        text = "Πρωινός έλεγχος" if e.type == "check.morning_done" else describe(e, names)
        out[key] = {"at": _hhmm(e.at), "text": text}
    return out


def _last_before(conn, day_start, until_id, last):
    """The last job before today for each member who has none today: {"date": "DD/MM", "text"}."""
    out = {key: None for key in MEMBERS}
    if all(last[key] is not None for key in MEMBERS):
        return out
    names = load_names(conn)
    solved = views.resolved_flag_ids(conn, until_id)
    for e in read_events(conn, until_id=until_id, types=TASK_TYPES + ("agent.planned",)):
        key, _ = _team(e)
        if e.at >= day_start or not _is_task(e) or key not in out or e.id in solved:
            continue
        text = "Πρωινός έλεγχος" if e.type == "check.morning_done" else describe(e, names)
        out[key] = {"date": _local(e.at).strftime("%d/%m"), "text": text}
    return {key: (out[key] if last[key] is None else None) for key in MEMBERS}


def _credit(conn, until_id, bundles):
    """approval_id -> what the credit request says, for each pending request_credit."""
    names_product = load_names(conn)["product"]
    out = {}
    for e in views.pending_approvals(conn, until_id=until_id):
        p = e.payload
        if p.get("action") != "request_credit":
            continue
        ref = str(p.get("ref") or "")
        invoice_id = (p.get("args") or {}).get("invoice_id") or (
            ref[len("invoice:"):] if ref.startswith("invoice:") else None)
        d = credit_details(conn, invoice_id) if invoice_id else None
        matched = (bundles.get(invoice_id) or {}).get("matched")
        if d is None or matched is None:
            continue
        shortages = [i for i in matched.payload.get("issues", []) if i.get("kind") == "shortage"]
        if not shortages:
            continue
        s = shortages[0]
        missing = s["expected"] - s["actual"]
        row = conn.execute("SELECT vat_rate FROM products WHERE id = ?", (s["product_id"],)).fetchone()
        out[p.get("approval_id")] = {
            "invoice_id": invoice_id,
            "label": _label(bundles.get(invoice_id, {}), invoice_id),
            "supplier": d["supplier"]["name"],
            "subject": d["subject"],
            "amount_cents": d["amount_cents"],
            "product": names_product.get(s["product_id"]) or str(s["product_id"]),
            "more": len(shortages) - 1,
            "order_id": matched.payload.get("order_id"),
            "expected": s["expected"], "actual": s["actual"], "missing": missing,
            "unit_cents": round(s["amount_cents"] / missing) if missing else 0,
            "vat_rate": int(row["vat_rate"]) if row else 0,
        }
    return out


def _recent_and_month(conn, until_id, day_start, today, bundles):
    """The 3 latest invoices read before today, and this month's invoices (by invoice date)."""
    latest = {}
    for e in read_events(conn, until_id=until_id, types=["invoice.read"]):
        latest[e.payload.get("invoice_id")] = e
    before = sorted((e for e in latest.values() if e.at < day_start),
                    key=lambda e: (e.at, e.id), reverse=True)[:RECENT_MAX]
    recent = [{"label": _label(bundles.get(e.payload.get("invoice_id"), {}), e.payload.get("invoice_id")),
               "date": _local(e.at).date().isoformat()} for e in before]
    prefix = today.strftime("%Y-%m")
    mine = [e for e in latest.values() if str(e.payload.get("date") or "")[:7] == prefix]
    month = {"name": MONTHS[today.month - 1], "count": len(mine),
             "total_cents": sum(e.payload.get("total_cents") or 0 for e in mine)}
    return recent, month


def _last_paid(conn, until_id, bundles):
    undone = undo.undone_ids(conn, until_id)
    for e in reversed(read_events(conn, until_id=until_id, types=["payment.recorded"])):
        if e.payload.get("method") == "not_yet" or e.id in undone:
            continue
        iid = e.payload.get("invoice_id")
        return {"label": _label(bundles.get(iid, {}), iid), "amount_cents": e.payload.get("amount_cents"),
                "date": _local(e.at).date().isoformat()}
    return None


@router.get("/api/screen/home")
def home(until_id: int | None = None):
    conn = _db()
    try:
        now = _reference(conn, until_id)
        day_start = greek_day_start_utc(now)
        today = (now + greek_offset(now)).date()
        suppliers = load_names(conn)["supplier"]
        bundles = _bundles(conn, until_id)

        invoices = invoices_today(until_id)["invoices"]
        reminders = _reminders(conn, until_id, today, now, bundles, suppliers)
        items = [r for r in reminders if r["can"]][:UNDO_MAX]
        index = undo.Index(conn, until_id)
        can_total = sum(1 for e in _since(conn, day_start, until_id, undo.UNDOABLE)
                        if index.state(e, now) == "can")

        def pick(r, *keys):
            return {k: r[k] for k in keys}

        keys = ("label", "supplier", "amount_cents", "due_on", "days")
        recent, month = _recent_and_month(conn, until_id, day_start, today, bundles)
        last = _last(conn, day_start, until_id)
        return {
            "today": today.isoformat(),
            "credit": _credit(conn, until_id, bundles),
            "sales": _sales(conn, until_id),
            "stock": _stock(conn, until_id, today),
            "order": _order(conn, until_id, suppliers),
            "invoices": {
                "need_you": sum(1 for i in invoices if i["level"] != "ok"),
                "today": len(invoices),
                "docs": [{"label": i["label"], "need": i["level"] != "ok"}
                         for i in invoices[:DOCS_MAX]],
                "recent": recent, "month": month},
            "payments": {"next": pick(reminders[0], *keys) if reminders else None,
                         "last_paid": _last_paid(conn, until_id, bundles)},
            "undo": {"can_total": max(can_total, len(items)),
                     "items": [{"event_id": r["event_id"], **pick(r, *keys)} for r in items]},
            "iban": _iban(conn, day_start, until_id, bundles, suppliers),
            "last": last,
            "last_before": _last_before(conn, day_start, until_id, last),
        }
    finally:
        conn.close()
