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

from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.api_b1 import (
    TASK_TYPES, _bundles, _db, _hhmm, _is_task, _label, _reference, _since, _team,
    invoices_today,
)
from xangx.screen.describe import describe, load_names
from xangx.shared import undo
from xangx.shared.events import read_events
from xangx.stock.trends import sales_trends

router = fastapi.APIRouter()

PLURAL_DAYS = ("Δευτέρες", "Τρίτες", "Τετάρτες", "Πέμπτες", "Παρασκευές", "Σάββατα", "Κυριακές")
MEMBERS = ("master", "stock", "orders", "invoices", "accountant")
UNDO_MAX = 3
EXPIRING_MAX = 3
DOCS_MAX = 3


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
            if e.payload.get("method") != "not_yet"}
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
    out = {key: None for key in MEMBERS}
    for e in events:
        key, _ = _team(e)
        if not _is_task(e) or key not in out:
            continue
        text = "Πρωινός έλεγχος" if e.type == "check.morning_done" else describe(e, names)
        out[key] = {"at": _hhmm(e.at), "text": text}
    return out


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
        return {
            "sales": _sales(conn, until_id),
            "stock": _stock(conn, until_id, today),
            "order": _order(conn, until_id, suppliers),
            "invoices": {
                "need_you": sum(1 for i in invoices if i["level"] != "ok"),
                "today": len(invoices),
                "docs": [{"label": i["label"], "need": i["level"] != "ok"}
                         for i in invoices[:DOCS_MAX]]},
            "payments": {"next": pick(reminders[0], *keys) if reminders else None},
            "undo": {"can_total": max(can_total, len(items)),
                     "items": [{"event_id": r["event_id"], **pick(r, *keys)} for r in items]},
            "iban": _iban(conn, day_start, until_id, bundles, suppliers),
            "last": _last(conn, day_start, until_id),
        }
    finally:
        conn.close()
