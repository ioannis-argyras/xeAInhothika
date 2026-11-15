"""Data for the Invoices page (all invoices by month, decisions, payments) and the extra fields of
one invoice (credit box, payment, history, VAT).

Included by the server with `app.include_router(router)`. Route: /api/screen/invoices-page.
`invoice_extras` is called by `api_b1.invoice_detail`. Read only. Amounts are whole cents. A whole
IBAN is never sent: only its last four characters. No email address is part of any answer.
"""

from datetime import date

import fastapi

from xangx.accountant.trends import purchase_trends
from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.api_b1 import (
    _bundles, _clock, _db, _hhmm, _label, _last_id, _local, _reference, _since, _status, _team,
    _via, invoices_today,
)
from xangx.screen.api_home import MONTHS, _iban, _recent_and_month, _reminders
from xangx.screen.credit_box import credit_boxes
from xangx.screen.describe import describe, load_names
from xangx.shared import payments, undo, views
from xangx.shared.events import read_events
from xangx.shared.money import format_eur

router = fastapi.APIRouter()

HISTORY_TYPES = (
    "invoice.received", "invoice.read", "invoice.matched", "invoice.mydata_checked",
    "price.changed", "supplier.new_iban_seen", "payment.reminder_set", "payment.asked",
    "payment.recorded", "credit.requested", "exception.flagged",
)


def _ddmmyyyy(iso_day):
    parts = str(iso_day or "")[:10].split("-")
    return "/".join(reversed(parts)) if len(parts) == 3 else str(iso_day or "")


def paid_map(conn, until_id):
    """invoice_id -> {"method", "date"} of the latest payment.recorded that was not undone and is
    not "not yet"."""
    undone = undo.undone_ids(conn, until_id)
    out = {}
    for e in read_events(conn, until_id=until_id, types=["payment.recorded"]):
        if e.payload.get("method") == "not_yet" or e.id in undone:
            continue
        out[e.payload.get("invoice_id")] = {
            "method": e.payload.get("method"), "date": _local(e.at).date().isoformat()}
    return out


def _pending_by_invoice(conn, until_id):
    """invoice_id -> the pending request_credit approval event."""
    out = {}
    for e in views.pending_approvals(conn, until_id=until_id):
        p = e.payload
        if p.get("action") != "request_credit":
            continue
        ref = str(p.get("ref") or "")
        iid = (p.get("args") or {}).get("invoice_id") or (
            ref[len("invoice:"):] if ref.startswith("invoice:") else None)
        if iid:
            out.setdefault(iid, e)
    return out


def _need(conn, day_start, today_iso, until_id, bundles, suppliers, names, boxes):
    out = []
    for approval_id, c in boxes.items():
        out.append({"kind": "credit", "approval_id": approval_id, "invoice_id": c["invoice_id"],
                    "label": c["label"], "supplier": c["supplier"],
                    "amount_cents": c["amount_cents"], "product": c["product"],
                    "missing": c["missing"], "order_id": c["order_id"]})
    ibans = _iban(conn, day_start, until_id, bundles, suppliers)
    seen = _since(conn, day_start, until_id, ["supplier.new_iban_seen"])
    for e, row in zip(seen, ibans):  # _iban gives one row per event, in the same order
        out.append({"kind": "iban", "invoice_id": e.payload.get("invoice_id"),
                    "label": row["invoice_label"], "supplier": row["supplier"],
                    "known_last4": row["known_last4"], "new_last4": row["new_last4"]})
    for item in payments.open_payments(conn, day_start, today_iso, until_id=until_id):
        _, line = payments.card_texts(item, names)
        supplier = suppliers.get(item["supplier_id"]) or str(item["supplier_id"])
        prefix = f"{supplier}, {format_eur(item['amount_cents'] or 0)}"
        how = line[len(prefix) + 2:] if line.startswith(prefix + ", ") else ""
        iid = item["invoice_id"]
        out.append({"kind": "pay", "invoice_id": iid, "label": _label(bundles.get(iid, {}), iid),
                    "supplier": supplier, "amount_cents": item["amount_cents"], "how": how})
    return out


def _payment_items(conn, until_id, today, now, bundles, suppliers, day_start, today_iso):
    """Payment reminders not undone and not paid, plus today's "did you pay?" questions."""
    owner = {e.id: e.payload.get("invoice_id")
             for e in read_events(conn, until_id=until_id, types=["payment.reminder_set"])}
    items = []
    for r in _reminders(conn, until_id, today, now, bundles, suppliers):
        iid = owner.get(r["event_id"])
        items.append({"invoice_id": iid, "label": r["label"], "supplier": r["supplier"],
                      "amount_cents": r["amount_cents"], "due_on": r["due_on"], "days": r["days"]})
    have = {i["invoice_id"] for i in items}
    for q in payments.open_payments(conn, day_start, today_iso, until_id=until_id):
        iid = q["invoice_id"]
        if q["kind"] != "asked" or iid in have:
            continue
        items.append({"invoice_id": iid, "label": _label(bundles.get(iid, {}), iid),
                      "supplier": suppliers.get(q["supplier_id"]), "amount_cents": q["amount_cents"],
                      "due_on": today_iso, "days": 0})
    items.sort(key=lambda i: i["due_on"])  # stable: the nearest first
    return {"total_cents": sum(i["amount_cents"] or 0 for i in items),
            "until": max((i["due_on"] for i in items), default=None), "items": items}


def _row(conn, iid, b, read, pending, paid, due_by_invoice, first):
    level, status = _status(conn, iid, b, pending.get(iid))
    if iid in pending or b.get("iban"):
        level = "need"
    arrived = {"date": _local(first.at).date().isoformat(), "at": _hhmm(first.at),
               "via": _via(b.get("received"))}
    if iid in paid:
        pay = {"state": "paid", "date": paid[iid]["date"]}
    elif iid in due_by_invoice:
        d = due_by_invoice[iid]
        pay = {"state": "due", "days": d["days"], "due_on": d["due_on"]}
    else:
        pay = None
    return {"invoice_id": iid, "label": _label(b, iid), "supplier": None,
            "arrived": arrived, "lines": len((read.payload.get("lines") or [])) if read else 0,
            "level": level, "status": status,
            "total_cents": (read.payload.get("total_cents") or 0) if read else 0, "pay": pay}


def _group(key, name, rows):
    return {"key": key, "name": name, "count": len(rows),
            "total_cents": sum(r["total_cents"] for _, r in rows), "rows": [r for _, r in rows]}


@router.get("/api/screen/invoices-page")
def invoices_page(until_id: int | None = None):
    conn = _db()
    try:
        now = _reference(conn, until_id)
        day_start = greek_day_start_utc(now)
        today = (now + greek_offset(now)).date()
        today_iso = today.isoformat()
        names = load_names(conn)
        suppliers = names["supplier"]
        bundles = _bundles(conn, until_id)
        todays = invoices_today(until_id)["invoices"]
        today_ids = {i["invoice_id"] for i in todays}

        pending = _pending_by_invoice(conn, until_id)
        boxes = credit_boxes(conn, until_id, bundles)
        pay_items = _payment_items(conn, until_id, today, now, bundles, suppliers, day_start,
                                   today_iso)
        due_by_invoice = {i["invoice_id"]: i for i in pay_items["items"] if i["invoice_id"]}
        paid = paid_map(conn, until_id)

        reads = {iid: b["read"] for iid, b in bundles.items() if b.get("read")}
        rows = {}  # invoice_id -> (sort key, row, paper month)
        for iid, b in bundles.items():
            read = b.get("read")
            first = b.get("received") or read or b.get("dup")
            if first is None or (read is None and iid not in today_ids):
                continue
            row = _row(conn, iid, b, read, pending, paid, due_by_invoice, first)
            if read is not None:
                afm = read.payload.get("supplier_afm")
                found = conn.execute("SELECT name FROM suppliers WHERE afm = ?", (afm,)).fetchone()
                row["supplier"] = found["name"] if found else "Άγνωστος προμηθευτής"
            paper = str(read.payload.get("date") or "")[:10] if read else ""
            rows[iid] = ((paper, first.id), row, paper[:7] or row["arrived"]["date"][:7])

        groups = []
        mine = sorted((v for k, v in rows.items() if k in today_ids), key=lambda v: v[0],
                      reverse=True)
        if mine:
            groups.append(_group("today", "Σήμερα, " + _ddmmyyyy(today_iso),
                                 [(v[0], v[1]) for v in mine]))
        by_month = {}
        for iid, (sort, row, month) in rows.items():
            if iid not in today_ids:
                by_month.setdefault(month, []).append((sort, row))
        for month in sorted(by_month, reverse=True):
            year, num = month.split("-")
            by_month[month].sort(key=lambda v: v[0], reverse=True)
            groups.append(_group(month, f"{MONTHS[int(num) - 1]} {year}", by_month[month]))

        return {
            "today": today_iso,
            "counts": {"all": len(reads),
                       "suppliers": len({r.payload.get("supplier_afm") for r in reads.values()}),
                       "today": len(todays),
                       "need_you": sum(1 for i in todays if i["level"] != "ok")},
            "need": _need(conn, day_start, today_iso, until_id, bundles, suppliers, names, boxes),
            "ok_today": [{"label": i["label"], "supplier": i["supplier"]}
                         for i in todays if i["level"] == "ok"],
            "payments": pay_items,
            "months": groups,
            "trend_ref": _trend_ref(conn, until_id, bundles),
            "month_folder": _recent_and_month(conn, until_id, day_start, today, bundles)[1],
            "last_id": _last_id(conn, until_id),
            "paused": views.is_paused(conn, until_id=until_id),
            "now": _clock(conn, until_id),
        }
    finally:
        conn.close()


# ---------- the extra fields of one invoice ----------

def _payment(conn, iid, until_id, now, today, paid, day_start, invoice_date):
    undone = undo.undone_ids(conn, until_id)
    reminder = None
    for e in read_events(conn, until_id=until_id, types=["payment.reminder_set"]):
        if e.payload.get("invoice_id") == iid and e.id not in undone:
            reminder = e
    done = paid.get(iid)
    if reminder is None:
        return {"due_on": None, "days": None, "event_id": None, "undo": None,
                "terms_days": None, "reminder_at": None, "paid": done} if done else None
    due = str(reminder.payload.get("due_on") or "")[:10]
    terms = None
    try:
        terms = (date.fromisoformat(due) - date.fromisoformat(invoice_date)).days
    except ValueError:
        pass
    return {"due_on": due or None,
            "days": (date.fromisoformat(due) - today).days if due else None,
            "terms_days": terms if terms is not None and terms > 0 else None,
            "reminder_at": _hhmm(reminder.at) if reminder.at >= day_start
            else _local(reminder.at).strftime("%d/%m %H:%M"),
            "event_id": reminder.id,
            "undo": undo.Index(conn, until_id).state(reminder, now),
            "paid": done}


def _history(conn, iid, until_id, day_start, bundles, scene_amount, pending_here):
    labels = {i: _label(b, i) for i, b in bundles.items()}
    names = load_names(conn)
    read_ids = {e.id for e in read_events(conn, until_id=until_id, types=["invoice.read"])
                if e.payload.get("invoice_id") == iid}
    undone = undo.undone_ids(conn, until_id)
    out = []
    for e in read_events(conn, until_id=until_id, types=HISTORY_TYPES):
        p = e.payload
        mine = (p.get("invoice_id") == iid or e.subject == f"invoice:{iid}"
                or (e.type == "exception.flagged" and p.get("ref") == f"invoice:{iid}")
                or (e.type == "price.changed" and e.caused_by in read_ids))
        if not mine or (e.type == "payment.recorded" and e.id in undone):
            continue
        text = describe(e, names)
        for known, label in labels.items():
            text = text.replace(known, label)
        local = _local(e.at)
        at = _hhmm(e.at) if e.at >= day_start else local.strftime("%d/%m %H:%M")
        team = "owner" if e.actor == "owner" else _team(e)[0]
        out.append({"at": at, "team": team, "kind": e.type, "text": text})
    if pending_here:
        text = "Περιμένει εσένα: πιστωτικό" + (
            " " + format_eur(scene_amount) if scene_amount is not None else "")
        out.append({"at": "τώρα", "team": "you", "kind": "you", "text": text})
    return out


def _vat(read):
    if read is None:
        return []
    vat = {}
    for rate, cents in (read.payload.get("vat_by_rate") or {}).items():
        try:
            vat[int(float(rate))] = int(cents)
        except (TypeError, ValueError):
            continue
    return [{"rate": r, "cents": c} for r, c in sorted(vat.items(), reverse=True)]


def attach_cheaper(conn, iid, until_id, lines):
    """Give a line `cheaper` ({supplier, cents}) when supplier.cheaper_found, caused by this
    invoice, names another supplier with the same product cheaper. Nothing otherwise."""
    names = load_names(conn)["supplier"]
    found = {}
    for e in read_events(conn, until_id=until_id, types=["supplier.cheaper_found"]):
        p = e.payload
        if p.get("caused_by_invoice_id") == iid:
            found[p.get("product_id")] = {"supplier": names.get(p.get("to_supplier_id")),
                                          "cents": p.get("to_cents")}
    for line in lines:
        cheaper = found.get(line.get("product_id"))
        if cheaper and cheaper["supplier"] and cheaper["cents"] is not None:
            line["cheaper"] = cheaper


def _trend_ref(conn, until_id, bundles):
    """The invoice that carried the newest price change of the biggest change in the trends
    ({invoice_id, label}), or None."""
    changes = purchase_trends(conn, until_id=until_id)["changes"]
    if not changes:
        return None
    newest = None
    for iid, b in bundles.items():
        for e in b.get("prices", []):
            if e.payload.get("product_id") == changes[0]["product_id"] and (
                    newest is None or e.id > newest[0]):
                newest = (e.id, iid)
    return {"invoice_id": newest[1], "label": _label(bundles[newest[1]], newest[1])} if newest else None


def invoice_extras(conn, iid, until_id, bundles, pending):
    """via, credit_box, payment, history and vat of one invoice, for `invoice_detail`."""
    b = bundles[iid]
    now = _reference(conn, until_id)
    day_start = greek_day_start_utc(now)
    today = (now + greek_offset(now)).date()
    box = None
    if pending is not None:
        approval_id = pending.payload.get("approval_id")
        found = credit_boxes(conn, until_id, bundles).get(approval_id)
        if found is not None:
            box = {**found, "approval_id": approval_id}
    amount = box["amount_cents"] if box else None
    return {
        "via": _via(b.get("received")),
        "credit_box": box,
        "payment": _payment(conn, iid, until_id, now, today, paid_map(conn, until_id), day_start,
                            str((b["read"].payload.get("date") if b.get("read") else "") or "")[:10]),
        "history": _history(conn, iid, until_id, day_start, bundles, amount, pending is not None),
        "vat": _vat(b.get("read")),
    }
