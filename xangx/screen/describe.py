"""One Greek sentence per event, for the live feed and the home screen."""

import re

from xangx.shared.money import format_eur

MONTHS_GENITIVE = ("Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου",
                   "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")


def load_names(conn):
    """Names of products, positions and suppliers from the reference tables."""
    names = {"product": {}, "position": {}, "supplier": {}, "invoice": {}}
    for table, key in (("products", "product"), ("positions", "position"),
                       ("suppliers", "supplier")):
        try:
            for row in conn.execute(f"SELECT id, name FROM {table}"):
                names[key][row["id"]] = row["name"]
        except Exception:
            pass
    try:
        rows = conn.execute(
            "SELECT json_extract(payload, '$.invoice_id') AS iid, "
            "json_extract(payload, '$.series') AS series, "
            "json_extract(payload, '$.number') AS number "
            "FROM events WHERE type = 'invoice.read'")
        for row in rows:
            label = _paper_number(row["series"], row["number"])
            if row["iid"] and label:
                names["invoice"][row["iid"]] = label
    except Exception:
        pass
    return names


def _paper_number(series, number):
    """The number printed on the paper, like Α-2271."""
    if series and number:
        return f"{series}-{number}"
    return str(number) if number else None


def _mask(text):
    """Never show a whole IBAN: keep only the last four characters."""
    return _IBAN.sub(lambda m: "IBAN …" + m.group(0)[-4:], text)


def _qty(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else str(number).replace(".", ",")


def _expiry_when(expires_on, at):
    """«Λήγει αύριο», counted from the day the event was written."""
    from datetime import date
    from xangx.stock.expiry import _greek_day

    left = (date.fromisoformat(expires_on) - _greek_day(at)).days
    if left < 0:
        return "Έχει λήξει"
    return {0: "Λήγει σήμερα", 1: "Λήγει αύριο"}.get(left, f"Λήγει σε {left} μέρες")


def describe(event, names=None):
    """A short Greek sentence for the event. Unknown types give the type itself."""
    names = names or {}

    def name(kind, ident):
        if ident is None:
            return "?"
        return names.get(kind, {}).get(ident) or str(ident)

    p = event.payload or {}
    t = event.type
    if t == "invoice.read" and p.get("invoice_id") and _paper_number(
            p.get("series"), p.get("number")):
        names = {**names, "invoice": {**names.get("invoice", {}),
                                      p["invoice_id"]: _paper_number(p["series"], p["number"])}}
    try:
        if t == "stock.expiring":
            text = (f"{_expiry_when(p['expires_on'], event.at)}: {_qty(p['qty'])} "
                    f"{name('product', p['product_id'])}")
        else:
            text = _describe(t, p, name)
    except Exception:
        text = None
    return _mask(str(text) if text else t)


def _describe(t, p, name):
    if t == "shelf.items_taken":
        return (f"{name('position', p['position_id'])}: πήραν "
                f"{_qty(p['taken'])} {name('product', p['product_id'])}")
    if t == "shelf.items_added":
        return (f"{name('position', p['position_id'])}: γέμισαν "
                f"{_qty(p['added'])} {name('product', p['product_id'])}")
    if t == "stock.moved" and p.get("reason") == "invoice":
        source = p.get("invoice_id") or p.get("ref")
        tail = f" από το τιμολόγιο {name('invoice', source)}" if source else " από τιμολόγιο"
        return f"Μπήκαν στην αποθήκη {_qty(p['qty'])} {name('product', p['product_id'])}{tail}"
    if t == "stock.moved":
        reasons = {"delivery": "παραλαβή", "sale": "πώληση", "waste": "φθορά",
                   "count": "καταμέτρηση", "correction": "διόρθωση"}
        sign = "+" if float(p["qty"]) > 0 else ""
        return (f"Απόθεμα {name('product', p['product_id'])}: {sign}{_qty(p['qty'])} "
                f"({reasons.get(p['reason'], p['reason'])})")
    if t == "stock.low_detected":
        return (f"{name('product', p['product_id'])} σε χαμηλό: "
                f"{_qty(p['on_hand'])} τεμάχια")
    if t == "delivery.started":
        return f"Ξεκίνησε η παραλαβή {p['delivery_id']} από {name('supplier', p['supplier_id'])}"
    if t == "delivery.item_scanned":
        return f"Σκαναρίστηκε το barcode {p['barcode']}"
    if t == "delivery.completed":
        short = len(p.get("shortages") or [])
        extra = f", {short} ελλείψεις" if short else ""
        return f"Τελείωσε η παραλαβή {p['delivery_id']}{extra}"
    if t == "order.proposed":
        return (f"Πρόταση παραγγελίας {p['order_id']} στο {name('supplier', p['supplier_id'])}: "
                f"{format_eur(p['net_cents'])}")
    if t == "order.sent":
        return f"Στάλθηκε η παραγγελία {p['order_id']}"
    if t == "order.reply_received":
        return f"Απάντηση στην παραγγελία {p['order_id']}: {p['summary']}"
    if t == "order.cancelled":
        return f"Ακυρώθηκε η παραγγελία {p['order_id']}"
    if t == "invoice.received":
        return f"Ήρθε τιμολόγιο {name('invoice', p['invoice_id'])}"
    if t == "invoice.read":
        return (f"Διαβάστηκε το τιμολόγιο {name('invoice', p['invoice_id'])}: "
                f"{format_eur(p['total_cents'])}")
    if t == "invoice.duplicate_suspected":
        return f"Το τιμολόγιο {p['invoice_id']} μοιάζει διπλό με το {p['other_invoice_id']}"
    if t == "invoice.matched":
        found = p.get("issues") or []
        issues = len(found)
        num = name("invoice", p["invoice_id"])
        if not p.get("delivery_id"):
            return f"Τιμολόγιο {num}: δεν βρέθηκε παραλαβή για ταίριασμα"
        if not issues:
            return f"Το τιμολόγιο {num} ταιριάζει με την παραλαβή"
        first = found[0].get("note") if isinstance(found[0], dict) else None
        if not first:
            return f"Το τιμολόγιο {num} έχει {issues} αποκλίσεις"
        more = f" και άλλες {issues - 1}" if issues > 1 else ""
        return f"Το τιμολόγιο {num}: {first}{more}"
    if t == "invoice.mydata_checked":
        num = name("invoice", p["invoice_id"])
        if p.get("error"):
            return f"Το myDATA δεν απάντησε για το τιμολόγιο {num}, θα ξαναδοκιμάσω"
        if p.get("found") and p.get("totals_match"):
            return f"myDATA: βρέθηκε το τιμολόγιο {num}, ίδιο σύνολο"
        if p.get("found"):
            return f"myDATA: βρέθηκε το τιμολόγιο {num}, ΔΙΑΦΟΡΑ στο σύνολο"
        return f"myDATA: δεν βρέθηκε το τιμολόγιο {num}"
    if t == "price.changed":
        return (f"Άλλαξε η τιμή του {name('product', p['product_id'])}: "
                f"{format_eur(p['old_cents'])} σε {format_eur(p['new_cents'])}")
    if t == "supplier.cheaper_found":
        return (f"Η {name('product', p['product_id'])} ακρίβυνε {str(p['pct_change']).replace('.', ',')}% "
                f"({name('supplier', p['from_supplier_id'])}). "
                f"Φθηνότερα: {name('supplier', p['to_supplier_id'])}, {format_eur(p['to_cents'])}.")
    if t == "supplier.new_iban_seen":
        return (f"Νέο IBAN (…{p['iban_last4']}) στον προμηθευτή "
                f"{name('supplier', p['supplier_id'])}")
    if t == "credit.requested":
        return f"Ζητήθηκε πιστωτικό για το τιμολόγιο {p['invoice_id']}"
    if t == "report.daily_created":
        return f"Ημερήσια αναφορά {p['date']}: πωλήσεις {format_eur(p['sales_cents'])}"
    if t == "report.month_created":
        month = MONTHS_GENITIVE[int(str(p["month"]).split("-")[1]) - 1]
        count = int(p["invoice_count"])
        return (f"Φάκελος {month}: {count} {'τιμολόγιο' if count == 1 else 'τιμολόγια'}, "
                f"{format_eur(p['total_cents'])}")
    if t == "exception.flagged":
        return f"Προσοχή: {p['note']}"
    if t == "payment.asked":
        return (f"Ρωτώ αν πληρώθηκε το {name('invoice', p['invoice_id'])} "
                f"({format_eur(p['amount_cents'])}, τοις μετρητοίς)")
    if t == "payment.recorded":
        num = name("invoice", p["invoice_id"])
        if p["method"] == "not_yet":
            return f"Το {num} δεν πληρώθηκε ακόμα, θα ξαναρωτήσω αύριο"
        how = {"cash": "μετρητά", "card": "κάρτα", "bank": "τράπεζα"}.get(p["method"], p["method"])
        return f"Πληρώθηκε το {num} με {how}"
    if t == "payment.reminder_set":
        return f"Υπενθύμιση πληρωμής για το τιμολόγιο {p['invoice_id']}"
    if t == "sale.recorded":
        return f"Πώληση {p['receipt_id']}: {format_eur(p['total_cents'])}"
    if t == "owner.said":
        return f"Εσύ: {p['text']}"
    if t == "agent.woke":
        return f"Ο συνεργάτης ξεκίνησε μόνος του: {p['reason']}"
    if t == "agent.planned" and p.get("via") == "mcp":
        first = (p.get("steps") or [{}])[0]
        return f"Άλλος agent ζήτησε μέσω MCP: {first.get('summary') or first.get('tool') or 'εργαλείο'}"
    if t == "agent.planned":
        return "Σχεδίασα " + str(len(p.get("steps") or [])) + " βήματα"
    if t in ("agent.tool_ran", "approval.requested"):
        return p["summary"]
    if t == "agent.said":
        return p["text"]
    if t == "approval.decided":
        return "Εγκρίθηκε" if p["decision"] == "yes" else "Απορρίφθηκε"
    if t == "notification.sent":
        return p["text"]
    if t == "system.paused":
        return "Όλες οι ενέργειες σταμάτησαν"
    if t == "system.resumed":
        return "Οι ενέργειες συνεχίζονται"
    if t == "action.undone":
        return f"Αναιρέθηκε η ενέργεια {p['event_id']}"
    if t == "check.morning_done":
        return "Έγινε ο πρωινός έλεγχος"
    return None
