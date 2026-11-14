"""Undo: what the owner can take back, and how it is written (as the opposite event).

Nothing is ever deleted. An undo is an `action.undone` event, plus the opposite event where
a number must go back (a stock move with the opposite qty, a cancelled order proposal and its
approval card). Views ignore what was undone: `Index.undone` is the set of undone event ids.
What already left the building (order sent, credit request, a message) is never undone.
"""

from datetime import datetime, timedelta, timezone

from xangx.shared.events import append_events, read_events

UNDO_HOURS = 24
# The kinds the owner can take back.
UNDOABLE = ("payment.reminder_set", "payment.recorded", "stock.moved", "order.proposed")
# The kinds that went out: the screen says so instead of offering an undo.
OUTBOUND = ("order.sent", "credit.requested", "notification.sent")
SENT_TEXT = "Έφυγε ήδη, δεν αναιρείται."
NOT_UNDOABLE_TEXT = "Αυτό δεν αναιρείται."
LATE_TEXT = "Πέρασαν πάνω από 24 ώρες, δεν αναιρείται πια."


def undone_ids(conn, until_id=None):
    """Ids of the events the owner (or the partner) took back."""
    out = set()
    for e in read_events(conn, until_id=until_id, types=["action.undone"]):
        eid = e.payload.get("event_id")
        if isinstance(eid, int):
            out.add(eid)
    return out


def _parse(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def is_undoable_kind(event):
    if event.type == "stock.moved":
        return event.payload.get("reason") == "invoice"
    return event.type in UNDOABLE


class Index:
    """What an undo decision needs, read once: undone events, sent and cancelled orders."""

    def __init__(self, conn, until_id=None):
        self.undone = undone_ids(conn, until_id)
        self.sent, self.cancelled = set(), set()
        for e in read_events(conn, until_id=until_id, types=["order.sent", "order.cancelled"]):
            (self.sent if e.type == "order.sent" else self.cancelled).add(e.payload.get("order_id"))

    def state(self, event, now=None):
        """"can" (button), "sent" (it left, say so), "done" (undone), or None (nothing to show)."""
        if event.type in OUTBOUND:
            return "sent"
        if not is_undoable_kind(event):
            return None
        if event.id in self.undone:
            return "done"
        if event.type == "order.proposed":
            oid = event.payload.get("order_id")
            if oid in self.sent:
                return "sent"
            if oid in self.cancelled:
                return None
        now = now or datetime.now(timezone.utc)
        if now - _parse(event.at) > timedelta(hours=UNDO_HOURS):
            return None
        return "can"


def _get_event(conn, event_id):
    found = read_events(conn, after_id=event_id - 1, until_id=event_id)
    return found[0] if found and found[0].id == event_id else None


def _pending_send_cards(conn, order_id):
    """approval_id of the open "send this order" cards for the order."""
    decided = {e.payload.get("approval_id") for e in read_events(conn, types=["approval.decided"])}
    out = []
    for e in read_events(conn, types=["approval.requested"]):
        p = e.payload
        args = p.get("args") or {}
        if p.get("approval_id") in decided:
            continue
        if args.get("order_id") == order_id or p.get("ref") == order_id:
            out.append(e)
    return out


def undo(conn, event_id, now=None):
    """Take an event back. Returns (http_status, body). A second call writes nothing new."""
    event = _get_event(conn, event_id)
    if event is None:
        return 404, {"detail": "Δεν βρήκα αυτή την ενέργεια."}
    state = Index(conn).state(event, now)
    if state == "sent":
        return 409, {"detail": SENT_TEXT}
    if not is_undoable_kind(event):
        return 400, {"detail": NOT_UNDOABLE_TEXT}
    if state is None:
        return 409, {"detail": LATE_TEXT}

    key = f"undo:{event_id}"
    items = []
    p = event.payload
    if event.type == "payment.reminder_set":
        how = "Ακυρώθηκε η υπενθύμιση πληρωμής."
    elif event.type == "payment.recorded":
        how = "Η πληρωμή σβήστηκε και ο Λογιστής θα ξαναρωτήσει."
    elif event.type == "stock.moved":
        how = "Το απόθεμα γύρισε πίσω με την αντίθετη κίνηση."
        items.append(dict(
            type="stock.moved", actor="invoices", caused_by=event.id,
            subject=f"product:{p['product_id']}", idem_key=key + ":stock",
            payload={"product_id": p["product_id"], "qty": -p["qty"], "reason": "undo",
                     "undoes": event.id}))
    else:  # order.proposed that was not sent
        order_id = p.get("order_id")
        how = "Η πρόταση παραγγελίας ακυρώθηκε."
        items.append(dict(
            type="order.cancelled", actor="owner", caused_by=event.id,
            subject=f"order:{order_id}", idem_key=key + ":order",
            payload={"order_id": order_id, "reason": "undo"}))
        for card in _pending_send_cards(conn, order_id):
            aid = card.payload.get("approval_id")
            items.append(dict(
                type="approval.decided", actor="owner", caused_by=card.id,
                subject=f"approval:{aid}", idem_key=f"approval.decided:{aid}",
                payload={"approval_id": aid, "decision": "no", "channel": "undo"}))
    items.append(dict(
        type="action.undone", actor="owner", caused_by=event.id, idem_key=key,
        subject=event.subject, payload={"event_id": event.id, "how": how}))
    results = append_events(conn, items)
    undone, created = results[-1]
    return 200, {"ok": True, "event_id": undone.id, "undone": event_id, "how": how,
                 "already": not created}
