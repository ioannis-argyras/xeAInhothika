"""Accountant logic. Actor "accountant" (credit.requested belongs to "invoices").

Nothing here pays anything or changes an IBAN: only reminders, flags and a credit request
that the owner approves first.
"""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from xangx.invoices import core as invoices
from xangx.shared import append_event, read_events
from xangx.shared import views
from xangx.shared.money import format_eur

ACTOR = "accountant"
NOT_ADVICE = "Τα φορολογικά είναι πληροφορία υποστήριξης, όχι συμβουλή λογιστή."


def _supplier(conn, afm):
    row = conn.execute("SELECT * FROM suppliers WHERE afm = ?", (afm,)).fetchone()
    return dict(row) if row else None


def _read(conn, invoice_id):
    return invoices._latest(conn, "invoice.read", invoice_id)


# ---------- 1. payment reminder ----------

def set_payment_reminder(conn, invoice_id):
    """payment.reminder_set: due on invoice date + supplier credit days. No payment."""
    read = _read(conn, invoice_id)
    if read is None:
        return {"summary": f"Το τιμολόγιο {invoice_id} δεν έχει διαβαστεί ακόμα.", "event_ids": []}
    p = read.payload
    supplier = _supplier(conn, p["supplier_afm"])
    if supplier is None:
        return {"summary": f"Άγνωστος προμηθευτής στο τιμολόγιο {invoice_id}.", "event_ids": []}
    try:
        due = date.fromisoformat(p["date"]) + timedelta(days=supplier["credit_days"] or 0)
    except ValueError:
        return {"summary": f"Η ημερομηνία του τιμολογίου {invoice_id} δεν διαβάστηκε.", "event_ids": []}
    event, created = append_event(
        conn, type="payment.reminder_set", actor=ACTOR,
        payload={"supplier_id": supplier["id"], "invoice_id": invoice_id,
                 "due_on": due.isoformat(), "amount_cents": p["total_cents"]},
        subject=f"invoice:{invoice_id}", idem_key=f"reminder:{invoice_id}", caused_by=read.id,
    )
    return {
        "summary": (f"Υπενθύμιση πληρωμής {format_eur(p['total_cents'])} στον "
                    f"{supplier['name']} για τις {due:%d/%m/%Y}."
                    + ("" if created else " (υπήρχε ήδη)")),
        "event_ids": [event.id] if created else [],
    }


# ---------- 2. credit request (needs the owner's yes) ----------

def _vat_rate(conn, product_id):
    row = conn.execute("SELECT vat_rate FROM products WHERE id = ?", (product_id,)).fetchone()
    return row["vat_rate"] if row else 0


def credit_details(conn, invoice_id):
    """What the credit request would say. Shortages only, amounts with VAT."""
    read = _read(conn, invoice_id)
    matched = invoices._latest(conn, "invoice.matched", invoice_id)
    if read is None or matched is None:
        return None
    supplier = _supplier(conn, read.payload["supplier_afm"])
    shortages = [i for i in matched.payload.get("issues", []) if i["kind"] == "shortage"]
    if not shortages or supplier is None:
        return None
    amount = 0
    for i in shortages:
        net = Decimal(i["amount_cents"])
        rate = Decimal(_vat_rate(conn, i["product_id"]))
        amount += int((net * (100 + rate) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    p = read.payload
    number = f"{p['series']}-{p['number']}"
    reasons = "; ".join(i["note"] for i in shortages)
    to = os.environ.get("XANGX_DEMO_SUPPLIER_EMAIL") or supplier["email"]
    body = (
        f"Καλησπέρα σας,\n\nστο τιμολόγιο {number} της {p['date']} χρεώθηκαν είδη που δεν "
        f"παραλάβαμε: {reasons}.\nΠαρακαλούμε για πιστωτικό τιμολόγιο "
        f"{format_eur(amount)} (με ΦΠΑ).\n\nΕυχαριστούμε,\nMini Market 24/7"
    )
    return {
        "supplier": supplier, "amount_cents": amount, "number": number, "to": to,
        "reasons": reasons, "subject": f"Αίτημα πιστωτικού για το τιμολόγιο {number}",
        "body": body, "read": read,
    }


def describe_credit(conn, invoice_id):
    d = credit_details(conn, invoice_id)
    if d is None:
        return {"summary": f"Αίτημα πιστωτικού για το τιμολόγιο {invoice_id}",
                "why": "Δεν βρέθηκε λείψη στο ταίριασμα.", "ref": f"invoice:{invoice_id}"}
    return {
        "summary": f"Αίτημα πιστωτικού {format_eur(d['amount_cents'])} στον {d['supplier']['name']}",
        "exact": f"Email προς {d['to']}, θέμα «{d['subject']}»:\n{d['body']}",
        "why": d["reasons"],
        "ref": f"invoice:{invoice_id}",
    }


def _send(to, subject, body, message_id):
    """Email with the orders module if it is there, else a file in data/outbox/."""
    try:
        from xangx.orders import mail  # Stavros's module, when it exists
        sender = getattr(mail, "send_email", None) or getattr(mail, "send_mail", None)
    except ModuleNotFoundError:
        sender = None
    if sender is not None:
        result = sender(to=to, subject=subject, body=body)
        if isinstance(result, dict) and result.get("message_id"):
            return result["message_id"], "email"
        return message_id, "email"
    outbox = invoices.data_dir() / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", message_id.strip("<>"))
    (outbox / f"{safe}.txt").write_text(
        f"To: {to}\nSubject: {subject}\nMessage-ID: {message_id}\n\n{body}\n", encoding="utf-8")
    return message_id, "outbox"


def request_credit(conn, invoice_id):
    """Runs only after the owner's yes. Sends the request and writes credit.requested."""
    d = credit_details(conn, invoice_id)
    if d is None:
        return {"summary": f"Δεν υπάρχει λείψη για πιστωτικό στο τιμολόγιο {invoice_id}.",
                "event_ids": []}
    existing = invoices._latest(conn, "credit.requested", invoice_id)
    if existing is not None:
        return {"summary": f"Το αίτημα πιστωτικού για το {d['number']} έχει ήδη σταλεί.",
                "event_ids": []}
    message_id, how = _send(d["to"], d["subject"], d["body"],
                            f"<credit-{invoice_id.lower()}@xangxothaika.local>")
    event, _ = append_event(
        conn, type="credit.requested", actor="invoices",
        payload={"invoice_id": invoice_id, "amount_cents": d["amount_cents"],
                 "reason": d["reasons"][:500], "message_id": message_id},
        subject=f"invoice:{invoice_id}", idem_key=f"credit:{invoice_id}",
        caused_by=d["read"].id,
    )
    where = "με email" if how == "email" else "στο φάκελο εξερχομένων (data/outbox)"
    return {"summary": f"Στάλθηκε αίτημα πιστωτικού {format_eur(d['amount_cents'])} στον "
                       f"{d['supplier']['name']} {where}.",
            "event_ids": [event.id]}


# ---------- 3. daily report ----------

def _greek_day_bounds(day):
    """UTC [start, end) of a Greek calendar day, as `at` strings."""
    from xangx.master.tools import greek_offset

    noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    off = greek_offset(noon)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - off
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return start.strftime(fmt), end.strftime(fmt)


def report_numbers(conn, day):
    start, end = _greek_day_bounds(day)
    sales_gross = sales_net = cost = 0
    vat = {r["id"]: r["vat_rate"] for r in conn.execute("SELECT id, vat_rate FROM products")}
    unit_cost = {}
    for e in read_events(conn, types=["sale.recorded"]):
        if not (start <= e.at < end):
            continue
        sales_gross += e.payload["total_cents"]
        for line in e.payload.get("lines", []):
            pid = line["product_id"]
            sales_net += round(line["gross_cents"] * 100 / (100 + vat.get(pid, 0)))
            if pid not in unit_cost:
                unit_cost[pid] = views.last_unit_price_cents(conn, pid) or 0
            cost += round(line["qty"] * unit_cost[pid])
    purchases = sum(e.payload["total_cents"] for e in read_events(conn, types=["invoice.read"])
                    if e.payload.get("date") == day.isoformat())
    exceptions = [
        {"kind": e.payload["kind"], "ref": e.payload["ref"], "note": e.payload["note"]}
        for e in read_events(conn, types=["exception.flagged"]) if start <= e.at < end
    ]
    return {"date": day.isoformat(), "sales_cents": sales_gross, "sales_net_cents": sales_net,
            "purchases_cents": purchases, "gross_profit_cents": sales_net - cost,
            "exceptions": exceptions}


def daily_report(conn, day):
    numbers = report_numbers(conn, day)
    event, _ = append_event(conn, type="report.daily_created", actor=ACTOR, payload=numbers,
                            subject=f"report:{numbers['date']}")
    n = len(numbers["exceptions"])
    return {
        "summary": (f"Αναφορά {day:%d/%m/%Y}: πωλήσεις {format_eur(numbers['sales_cents'])}, "
                    f"αγορές {format_eur(numbers['purchases_cents'])}, μικτό κέρδος "
                    f"{format_eur(numbers['gross_profit_cents'])}, εξαιρέσεις {n}. {NOT_ADVICE}"),
        "event_ids": [event.id],
    }


# ---------- 4. exceptions from the invoice events ----------

def flag_for(conn, event):
    """exception.flagged for new IBAN, bad arithmetic and duplicate invoice. Returns the event or None."""
    p = event.payload
    if event.type == "supplier.new_iban_seen":
        kind, ref = "new_iban", f"invoice:{p['invoice_id']}"
        note = (f"Νέο IBAN (…{p['iban_last4']}) σε γνωστό προμηθευτή. Επιβεβαίωση στο "
                f"τηλέφωνο που ήδη ξέρουμε, ποτέ σε αυτό του τιμολογίου.")
    elif event.type == "invoice.read" and event.actor == "invoices" and not p["checks"].get("arithmetic", True):
        kind, ref = "arithmetic", f"invoice:{p['invoice_id']}"
        note = f"Τα ποσά του τιμολογίου {p['series']}-{p['number']} δεν βγαίνουν."
    elif event.type == "invoice.duplicate_suspected":
        kind, ref = "duplicate_invoice", f"invoice:{p['invoice_id']}"
        note = f"Το τιμολόγιο {p['invoice_id']} μοιάζει διπλό με το {p['other_invoice_id']}."
    else:
        return None
    flagged, _ = append_event(
        conn, type="exception.flagged", actor=ACTOR,
        payload={"kind": kind, "ref": ref, "note": note},
        subject=ref, idem_key=f"flag:{kind}:{ref}", caused_by=event.id,
    )
    return flagged


FLAG_TYPES = ["supplier.new_iban_seen", "invoice.read", "invoice.duplicate_suspected"]
