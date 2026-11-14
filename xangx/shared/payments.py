"""Which invoices still wait for the owner's answer to "Did you pay? How?".

Read-only view over the events: payment.asked (cash invoices), payment.reminder_set
(credit invoices, with a due day) and payment.recorded (the owner's answer).
Nothing here pays anything: "paid" only means what the owner said.
"""

from xangx.shared.events import append_event, read_events
from xangx.shared.undo import undone_ids
from xangx.shared.money import format_eur

METHODS = ("cash", "card", "bank", "not_yet")
METHOD_TEXT = {"cash": "με μετρητά", "card": "με κάρτα", "bank": "με τράπεζα"}


def _ddmmyyyy(iso):
    parts = str(iso or "")[:10].split("-")
    return "/".join(reversed(parts)) if len(parts) == 3 else str(iso or "")


def open_payments(conn, day_start, today, until_id=None):
    """Invoices to ask about now, oldest question first.

    `day_start` is the start of the Greek day as a UTC `at` string, `today` the Greek date
    as YYYY-MM-DD. A cash invoice is asked until the owner answers cash/card/bank. "Not yet"
    hides it for the rest of that day only. A credit invoice is asked from its due day on.
    """
    events = read_events(
        conn, until_id=until_id,
        types=["payment.asked", "payment.reminder_set", "payment.recorded"])
    items = {}
    answered = {}
    undone = undone_ids(conn, until_id)  # taken back by the owner: as if it never happened
    for e in events:
        if e.id in undone:
            continue
        p = e.payload
        iid = p.get("invoice_id")
        if e.type == "payment.asked":
            items[iid] = {"invoice_id": iid, "supplier_id": p.get("supplier_id"),
                          "amount_cents": p.get("amount_cents"), "kind": "asked",
                          "due_on": None, "event_id": e.id}
        elif e.type == "payment.reminder_set":
            if iid not in items and str(p.get("due_on") or "9999")[:10] <= today:
                items[iid] = {"invoice_id": iid, "supplier_id": p.get("supplier_id"),
                              "amount_cents": p.get("amount_cents"), "kind": "due",
                              "due_on": str(p.get("due_on"))[:10], "event_id": e.id}
        else:
            answered[iid] = (p.get("method"), e.at)
    out = []
    for iid, item in items.items():
        method, at = answered.get(iid, (None, None))
        if method is not None and (method != "not_yet" or at >= day_start):
            continue
        out.append(item)
    return sorted(out, key=lambda i: i["event_id"])


def find_amount(conn, invoice_id):
    """The amount the owner is asked about, or None if nobody asked about this invoice."""
    undone = undone_ids(conn)
    for e in reversed(read_events(conn, types=["payment.asked", "payment.reminder_set"])):
        if e.payload.get("invoice_id") == invoice_id and e.id not in undone:
            return e.payload.get("amount_cents")
    return None


def card_texts(item, names):
    """Title and line for the card, in Greek. `names` comes from describe.load_names."""
    number = names.get("invoice", {}).get(item["invoice_id"]) or item["invoice_id"]
    supplier = names.get("supplier", {}).get(item["supplier_id"]) or str(item["supplier_id"])
    amount = format_eur(item["amount_cents"] or 0)
    if item["kind"] == "asked":
        how = "τοις μετρητοίς"
    elif item["due_on"]:
        how = "λήγει " + _ddmmyyyy(item["due_on"])
    else:
        how = ""
    line = f"{supplier}, {amount}" + (f", {how}" if how else "")
    return f"Πλήρωσες το τιμολόγιο {number};", line


def record_payment(conn, invoice_id, method, today):
    """Write what the owner said. A double click on the same answer writes one event.

    Returns the event, or None when nobody asked about this invoice.
    """
    amount = find_amount(conn, invoice_id)
    if amount is None:
        return None
    payload = {"invoice_id": invoice_id, "method": method, "amount_cents": amount}
    key = f"payment-recorded:{invoice_id}:{method}:{today}"
    event, _ = append_event(conn, type="payment.recorded", actor="owner", payload=payload,
                            idem_key=key)
    if event.id in undone_ids(conn):
        # The same answer was undone earlier: this one is a new answer, not the old event again.
        event, _ = append_event(conn, type="payment.recorded", actor="owner", payload=payload,
                                idem_key=f"{key}:after-{event.id}")
    return event
