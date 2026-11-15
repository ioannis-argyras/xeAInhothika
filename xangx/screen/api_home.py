"""Data for the home screen (B): sales, stock, order, invoices, payments, undo, IBAN, last job per member.

Included by the server with `app.include_router(router)`. Route: /api/screen/home. Read only.
Everything comes from the events, with the same "today" and "now" as the other screen routes
(`until_id` is the time machine). Amounts are whole cents. A whole IBAN is never sent: only its
last four characters.
"""

import json
import re
from datetime import date, datetime, timedelta

import fastapi

from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.api_b1 import (
    TASK_TYPES, _bundles, _db, _hhmm, _is_task, _label, _local, _parse, _reference, _since, _team,
    invoices_today,
)
from xangx.screen.credit_box import credit_boxes
from xangx.screen.describe import describe, load_names
from xangx.shared import undo, views
from xangx.shared.events import read_events
from xangx.shared.money import format_eur
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


JUST_NOW_MAX = 4
JOIN_MINUTES = 10  # a low-stock alert or a cheaper supplier joins the event that caused it
CAUSE_HOURS = 2
PACK_WORDS = ("κουτάκι", "μπουκάλι", "εξάδα", "συσκευασία", "φιάλη")
LEGAL_ENDING = re.compile(r"\s+(Α\.Ε\.|ΑΕ|Ο\.Ε\.|Ε\.Ε\.|ΕΠΕ|Ι\.Κ\.Ε\.|ΙΚΕ)\s*$")
# The member behind each kind of "just now" line: the icon and the name above the sentence.
MEMBER_VIEW = {"stock": ("package", "Αποθήκη"), "orders": ("truck", "Παραγγελίες"),
               "invoices": ("receipt", "Τιμολόγια"), "accountant": ("calculator", "Λογιστής"),
               "master": ("handshake", "Συνεργάτης")}
JUST_NOW_TYPES = ("shelf.items_taken", "stock.low_detected", "price.changed",
                  "supplier.cheaper_found", "invoice.read", "order.sent", "order.reply_received",
                  "credit.requested", "supplier.new_iban_seen", "delivery.completed",
                  "payment.recorded")
UNDO_KINDS = (
    (("payment.reminder_set", "payment.recorded"),
     {"label": "Πληρωμές και υπενθυμίσεις πληρωμής", "team": "accountant", "icon": "calculator"}),
    (("stock.moved",), {"label": "Κινήσεις αποθέματος", "team": "stock", "icon": "package"}),
    (("order.proposed",), {"label": "Προτάσεις παραγγελίας", "team": "orders", "icon": "truck"}),
)


def _first_word(name):
    return str(name or "").split(" ")[0]


def _short_product(name):
    """The words of the name before the first one with a digit or a pack word: "Cold brew"."""
    name = str(name or "")
    kept = []
    for word in name.split():
        if any(ch.isdigit() for ch in word) or word.lower() in PACK_WORDS:
            break
        kept.append(word)
    return " ".join(kept) or name


def _short_supplier(name):
    """The name without the legal ending (Α.Ε., Ο.Ε., ...)."""
    return LEGAL_ENDING.sub("", str(name or "")).strip()


def _short_position(name):
    return str(name or "").split(" · ")[0]


def _pct_text(pct):
    """8.8 -> "8,8%", 9.0 -> "9%"."""
    value = round(abs(float(pct)), 1)
    text = str(int(value)) if value == int(value) else str(value).replace(".", ",")
    return text + "%"


def _lower_first(text):
    text = str(text or "")
    return text[:1].lower() + text[1:]


def _part(text, em=None):
    return {"t": text, "em": em} if em else {"t": text}


def _qty(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number).replace(".", ",")


def _within(early, late, minutes):
    return timedelta(0) <= _parse(late.at) - _parse(early.at) <= timedelta(minutes=minutes)


def _just_now(conn, day_start, until_id, bundles, names):
    """The latest JUST_NOW_MAX events of today, newest first, each as one human sentence."""
    undone = undo.undone_ids(conn, until_id)
    events = [e for e in _since(conn, day_start, until_id, JUST_NOW_TYPES) if e.id not in undone]
    reads = {e.id: e for e in read_events(conn, until_id=until_id, types=["invoice.read"])}
    proposals = {}
    for e in read_events(conn, until_id=until_id, types=["order.proposed"]):
        proposals[e.payload.get("order_id")] = e
    supplier = names["supplier"]
    afm_names = {r["afm"]: r["name"] for r in conn.execute("SELECT afm, name FROM suppliers")}

    def label_of(invoice_id):
        return _label(bundles.get(invoice_id, {}), invoice_id) if invoice_id else None

    # Each low-stock alert joins the latest camera event of the same product before it, and each
    # cheaper supplier the latest price change of the same product before it (within JOIN_MINUTES).
    joined, used = {}, set()
    for follower, leader_type in (("stock.low_detected", "shelf.items_taken"),
                                  ("supplier.cheaper_found", "price.changed")):
        for f in events:
            if f.type != follower:
                continue
            leaders = [l for l in events if l.type == leader_type and l.id < f.id
                       and l.payload.get("product_id") == f.payload.get("product_id")
                       and _within(l, f, JOIN_MINUTES)]
            if leaders:
                joined[leaders[-1].id] = f
                used.add(f.id)

    out = []
    for e in events:
        p = e.payload
        if e.id in used:
            continue
        team = _team(e)[0]
        icon, who = MEMBER_VIEW.get(team, MEMBER_VIEW["master"])
        parts = None
        if e.type == "shelf.items_taken":
            text = (f"Πήραν {_qty(p.get('taken'))} {_short_product(names['product'].get(p.get('product_id')))}"
                    f" από το {_short_position(names['position'].get(p.get('position_id')))}.")
            low = joined.get(e.id)
            if low is not None:
                text += f" Έμεινε μόνο {_qty(low.payload.get('on_hand'))}."
            parts = [_part(text)]
            who = "Αποθήκη, από την κάμερα"
            icon = "camera"
        elif e.type == "price.changed":
            product = _short_product(names["product"].get(p.get("product_id")))
            cheaper = joined.get(e.id)
            rose = float(p.get("pct") or 0) >= 0
            parts = [_part(f"Η {product} " + ("ακρίβυνε " if rose else "φθήνυνε ")),
                     _part(_pct_text(p.get("pct") or 0), "bad" if rose else "good"),
                     _part(" στον " + _short_supplier(supplier.get(p.get("supplier_id"))) + ".")]
            invoice_id = None
            if cheaper is not None:
                cp = cheaper.payload
                invoice_id = cp.get("caused_by_invoice_id")
                parts += [_part(" Η " + _short_supplier(supplier.get(cp.get("to_supplier_id"))) + " την έχει "),
                          _part(format_eur(cp.get("to_cents") or 0), "good"), _part(".")]
            if not invoice_id and e.caused_by in reads:
                invoice_id = reads[e.caused_by].payload.get("invoice_id")
            label = label_of(invoice_id)
            who = "Τιμολόγια, στο " + label if label else "Τιμολόγια"
            icon = "trend-up"
        elif e.type == "invoice.read":
            label = label_of(p.get("invoice_id"))
            seller = _short_supplier(afm_names.get(p.get("supplier_afm"))) if p.get("supplier_afm") else ""
            lines = len(p.get("lines") or [])
            parts = [_part(f"Διάβασα το {label}" + (f" του {seller}" if seller else "")
                           + f": {lines} " + ("γραμμή" if lines == 1 else "γραμμές")
                           + f", {format_eur(p.get('total_cents') or 0)}.")]
            who, icon = "Τιμολόγια", "receipt"
        elif e.type == "order.sent":
            proposed = proposals.get(p.get("order_id"))
            tail = ""
            if proposed is not None:
                tail = (" στον " + _short_supplier(supplier.get(proposed.payload.get("supplier_id")))
                        + f": {format_eur(proposed.payload.get('net_cents') or 0)}")
            parts = [_part(f"Έστειλα την {p.get('order_id')}{tail}.")]
            who, icon = "Παραγγελίες, με το «ναι» σου", "paper-plane-tilt"
        elif e.type == "order.reply_received":
            summary = str(p.get("summary") or "").strip()
            if summary.startswith("Επιβεβαίωσε"):
                rest = _lower_first(summary[len("Επιβεβαίωσε"):].strip()).rstrip(".")
                text = (f"Ο προμηθευτής επιβεβαίωσε την παραγγελία {p.get('order_id')}"
                        + (f" {rest}" if rest else "") + ".")
            else:
                text = f"Ο προμηθευτής απάντησε για την {p.get('order_id')}: {summary.rstrip('.')}."
            parts = [_part(text)]
            who, icon = "Παραγγελίες, απάντηση με email", "envelope-open"
        elif e.type in ("stock.low_detected", "supplier.cheaper_found"):
            continue  # only as part of the event that caused them
        elif e.type == "payment.recorded" and p.get("method") == "not_yet":
            continue
        else:
            parts = [_part(describe(e, names))]
        out.append({"hhmm": _hhmm(e.at), "team": team, "icon": icon, "who": who,
                    "parts": parts, "kind": e.type})
    return out[::-1][:JUST_NOW_MAX]


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


def _camera_lows(conn, day_start, until_id):
    """Today's stock.low_detected, the latest per product, newest first, each with what the camera
    saw just before: {product_id: {"on_hand", "camera": {"taken", "hhmm"} or None}}."""
    takes = _since(conn, day_start, until_id, ["shelf.items_taken"])
    latest = {}
    for e in _since(conn, day_start, until_id, ["stock.low_detected"]):
        before = [t for t in takes if t.id < e.id and t.payload.get("product_id") == e.payload.get("product_id")]
        camera = ({"taken": before[-1].payload.get("taken"), "hhmm": _hhmm(before[-1].at)}
                  if before else None)
        latest.pop(e.payload.get("product_id"), None)
        latest[e.payload.get("product_id")] = {"on_hand": e.payload.get("on_hand"), "camera": camera}
    return dict(reversed(list(latest.items())))


def _stock(conn, until_id, today, day_start):
    from xangx.server.app import _stock_levels  # lazy: the server imports this module

    found = []
    for product_id, on_hand in _stock_levels(conn, until_id).items():
        row = conn.execute(
            "SELECT name, low_threshold FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None or row["low_threshold"] is None or on_hand > row["low_threshold"]:
            continue
        found.append((product_id, {"name": row["name"], "short": _first_word(row["name"]),
                                   "on_hand": on_hand, "low": row["low_threshold"], "camera": None}))
    found.sort(key=lambda item: item[1]["on_hand"] - item[1]["low"])
    rest = dict(found)
    low = []
    for product_id, seen in _camera_lows(conn, day_start, until_id).items():
        entry = rest.pop(product_id, None)
        if entry is None:
            row = conn.execute(
                "SELECT name, low_threshold FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None or row["low_threshold"] is None:
                continue
            entry = {"name": row["name"], "on_hand": seen["on_hand"], "low": row["low_threshold"]}
        entry["short"] = _short_product(entry["name"])
        entry["camera"] = seen["camera"]
        low.append(entry)
    low += list(rest.values())

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


def _since_text(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _cause(conn, until_id, order_id, names):
    """What the camera saw before the proposal: {"hhmm", "parts"} or None."""
    proposals = [e for e in read_events(conn, until_id=until_id, types=["order.proposed"])
                 if e.payload.get("order_id") == order_id]
    if not proposals:
        return None
    proposed = proposals[-1]
    wanted = {line.get("product_id") for line in proposed.payload.get("lines") or []}
    start = _since_text(_parse(proposed.at) - timedelta(hours=CAUSE_HOURS))
    before = [e for e in _since(conn, start, proposed.id, ["shelf.items_taken", "stock.low_detected"])
              if e.id < proposed.id and e.payload.get("product_id") in wanted]
    takes = [e for e in before if e.type == "shelf.items_taken"]
    if not takes:
        return None
    take = takes[-1]
    pid = take.payload.get("product_id")
    lows = [e for e in before if e.type == "stock.low_detected" and e.payload.get("product_id") == pid]
    tail = f" από το {_short_position(names['position'].get(take.payload.get('position_id')))}."
    if lows:
        tail += f" Έμεινε μόνο {_qty(lows[-1].payload.get('on_hand'))}."
    hhmm = _hhmm(take.at)
    product = _short_product(names["product"].get(pid))
    return {"hhmm": hhmm, "parts": [
        _part(f"Στις {hhmm} η κάμερα είδε ότι "),
        _part(f"πήραν {_qty(take.payload.get('taken'))} {product}", "b"), _part(tail)]}


def _order_extras(conn, until_id, day_start, order, names):
    """Adds "reply" (the supplier's last answer today) and "cause" (only for a proposal)."""
    replies = [e for e in _since(conn, day_start, until_id, ["order.reply_received"])
               if e.payload.get("order_id") == order["order_id"]]
    order["reply"] = ({"summary": replies[-1].payload.get("summary"), "hhmm": _hhmm(replies[-1].at)}
                      if replies else None)
    order["cause"] = _cause(conn, until_id, order["order_id"], names) if order["step"] == "proposed" else None
    return order


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


def _woke_text(conn, until_id, event, bundles, names):
    """The partner's footer: what woke it, short, or the usual sentence."""
    ids = [i for i in (event.payload.get("trigger_ids") or []) if isinstance(i, int)]
    triggers = []
    if ids:
        triggers = [t for t in read_events(conn, after_id=min(ids) - 1, until_id=max(ids)) if t.id in ids]
    prices = [t for t in triggers if t.type in ("price.changed", "supplier.cheaper_found")]
    if prices:
        invoice_id = next((t.payload.get("caused_by_invoice_id") for t in prices
                           if t.payload.get("caused_by_invoice_id")), None)
        for t in prices:
            if invoice_id or not t.caused_by:
                continue
            read = read_events(conn, after_id=t.caused_by - 1, until_id=t.caused_by, types=["invoice.read"])
            invoice_id = read[0].payload.get("invoice_id") if read else None
        label = _label(bundles.get(invoice_id, {}), invoice_id) if invoice_id else None
        return "Είδε αλλαγή τιμής" + (f" στο {label}" if label else "")
    if any(t.type in ("shelf.items_taken", "stock.low_detected") for t in triggers):
        return "Είδε χαμηλό απόθεμα στο ράφι"
    return describe(event, names)


def _last(conn, day_start, until_id, bundles):
    """The last job of today for each team member: {"at": "HH:MM", "text": ...} or None."""
    names = load_names(conn)
    events = _since(conn, day_start, until_id, TASK_TYPES + ("agent.planned",))
    solved = views.resolved_flag_ids(conn, until_id)
    out = {key: None for key in MEMBERS}
    for e in events:
        key, _ = _team(e)
        if not _is_task(e) or key not in out or e.id in solved:
            continue
        if e.type == "check.morning_done":
            text = "Πρωινός έλεγχος"
        elif e.type == "agent.woke":
            text = _woke_text(conn, until_id, e, bundles, names)
        else:
            text = describe(e, names)
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


def _undo_kinds():
    return [dict(kind) for types, kind in UNDO_KINDS if any(t in undo.UNDOABLE for t in types)]


@router.get("/api/screen/home")
def home(until_id: int | None = None):
    conn = _db()
    try:
        now = _reference(conn, until_id)
        day_start = greek_day_start_utc(now)
        today = (now + greek_offset(now)).date()
        names = load_names(conn)
        suppliers = names["supplier"]
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
        last = _last(conn, day_start, until_id, bundles)
        order = _order(conn, until_id, suppliers)
        if order is not None:
            order = _order_extras(conn, until_id, day_start, order, names)
        return {
            "today": today.isoformat(),
            "credit": credit_boxes(conn, until_id, bundles),
            "sales": _sales(conn, until_id),
            "stock": _stock(conn, until_id, today, day_start),
            "order": order,
            "just_now": _just_now(conn, day_start, until_id, bundles, names),
            "invoices": {
                "need_you": sum(1 for i in invoices if i["level"] != "ok"),
                "today": len(invoices),
                "docs": [{"label": i["label"], "need": i["level"] != "ok"}
                         for i in invoices[:DOCS_MAX]],
                "recent": recent, "month": month},
            "payments": {"next": pick(reminders[0], *keys) if reminders else None,
                         "last_paid": _last_paid(conn, until_id, bundles)},
            "undo": {"can_total": max(can_total, len(items)),
                     "items": [{"event_id": r["event_id"], **pick(r, *keys)} for r in items],
                     "hours": undo.UNDO_HOURS, "kinds": _undo_kinds()},
            "iban": _iban(conn, day_start, until_id, bundles, suppliers),
            "last": last,
            "last_before": _last_before(conn, day_start, until_id, last),
        }
    finally:
        conn.close()
