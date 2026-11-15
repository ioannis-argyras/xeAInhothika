"""Invoices: file in -> read -> checks without AI -> match -> price / IBAN / myDATA.

Actor "invoices". The hidden-instructions flag is written by the accountant actor
(exception.flagged belongs to it). Amounts are integer cents.
"""

import hashlib
import json
import os
import re
import shutil
from datetime import date as _date, timedelta
from pathlib import Path

from xangx.demo.catalog_data import afm_valid
from xangx.invoices import mydata
from xangx.invoices.read import read_invoice
from xangx.shared import append_event, read_events
from xangx.shared import views
from xangx.shared.money import format_eur

ACTOR = "invoices"
TOLERANCE_CENTS = 2
PRICE_DIFF_PCT = 2.0   # vs the order, for invoice.matched
PRICE_CHANGE_PCT = 3.0  # vs the previous invoice, for price.changed
INBOX_EXT = {".pdf": "pdf", ".jpg": "photo", ".jpeg": "photo", ".png": "photo"}


def data_dir():
    return Path(os.environ.get("XANGX_DATA_DIR") or "data")


# ---------- small readers over the events ----------

def _latest(conn, type, invoice_id):
    found = None
    for e in read_events(conn, types=[type]):
        if e.payload.get("invoice_id") == invoice_id:
            found = e
    return found


def _supplier_by_afm(conn, afm):
    row = conn.execute("SELECT * FROM suppliers WHERE afm = ?", (afm,)).fetchone()
    return dict(row) if row else None


def _products(conn):
    return [dict(r) for r in conn.execute("SELECT id, name FROM products")]


def _norm(text):
    return re.sub(r"[^\w]+", " ", (text or "").casefold()).strip()


def guess_product(conn, supplier_id, name):
    """Product id for an invoice line name: exact name, supplier code, or best word overlap."""
    wanted = _norm(name)
    if not wanted:
        return None
    products = _products(conn)
    for p in products:
        if _norm(p["name"]) == wanted:
            return p["id"]
    if supplier_id:
        for row in conn.execute(
            "SELECT product_id, supplier_code FROM supplier_products WHERE supplier_id = ?",
            (supplier_id,),
        ):
            code = _norm(row["supplier_code"])
            if code and code in wanted:
                return row["product_id"]
    words = set(wanted.split())
    best, best_score = None, 0.0
    for p in products:
        pw = set(_norm(p["name"]).split())
        if not pw:
            continue
        score = len(words & pw) / len(words | pw)
        if score > best_score:
            best, best_score = p["id"], score
    return best if best_score >= 0.6 else None


# ---------- 1. file in ----------

def ingest_file(conn, path, source=None, sender=None):
    """Move one inbox file to data/files/<sha256>.<ext> and write invoice.received.

    source/sender: set by the email intake ("email", the From address).
    Returns (event, created). The same file twice gives one event.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in INBOX_EXT:
        raise ValueError("not an invoice file: " + path.name)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    files = data_dir() / "files"
    files.mkdir(parents=True, exist_ok=True)
    target = files / f"{sha}{ext}"
    if not target.exists():
        shutil.copyfile(str(path), str(target))
    invoice_id = f"INV-{sha[:8]}"
    payload = {"invoice_id": invoice_id, "source": source or INBOX_EXT[ext],
               "file_ref": f"files/{sha}{ext}", "sha256": sha}
    if sender:
        payload["from"] = sender
    result = append_event(
        conn, type="invoice.received", actor=ACTOR, payload=payload,
        subject=f"invoice:{invoice_id}", idem_key=f"file:{sha}",
    )
    # Only now: if the event could not be written (e.g. "database is locked"), the file stays
    # in the inbox and the next pass tries again, instead of the invoice being lost.
    path.unlink()
    return result


def scan_inbox(conn):
    """Ingest every invoice file in data/inbox/. Returns the new invoice ids."""
    inbox = data_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    new = []
    for path in sorted(inbox.iterdir()):
        if path.is_file() and path.suffix.lower() in INBOX_EXT:
            event, created = ingest_file(conn, path)
            if created:
                new.append(event.payload["invoice_id"])
    return new


# ---------- 2. checks without AI ----------

def _int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


MAX_CREDIT_DAYS = 120


def clean_payment(raw, invoice_date):
    """Payment terms read from the paper, checked. Anything odd becomes "unknown"."""
    unknown = {"terms": "unknown", "credit_days": None, "due_date": None}
    if not isinstance(raw, dict):
        return unknown
    terms = raw.get("terms")
    if terms == "cash":
        return {"terms": "cash", "credit_days": None, "due_date": None}
    if terms != "credit":
        return unknown
    days, due = raw.get("credit_days"), raw.get("due_date")
    if days is not None:
        days = _int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) else None
        if days is None or not 0 <= days <= MAX_CREDIT_DAYS:
            return unknown
    if due is not None:
        try:
            start = _date.fromisoformat(invoice_date)
            due_day = _date.fromisoformat(str(due))
        except ValueError:
            return unknown
        if not start <= due_day <= start + timedelta(days=MAX_CREDIT_DAYS):
            return unknown
        due = due_day.isoformat()
    return {"terms": "credit", "credit_days": days, "due_date": due}


def clean(data):
    """Keep only the fields we expect, with the right types."""
    lines = []
    for line in data.get("lines") or []:
        lines.append({
            "name": str(line.get("name") or ""),
            "qty": line.get("qty") if isinstance(line.get("qty"), (int, float)) else None,
            "unit_cents": _int(line.get("unit_cents")),
            "net_cents": _int(line.get("net_cents")) or 0,
            "vat_rate": _int(line.get("vat_rate")),
        })
    vat = {str(k): _int(v) or 0 for k, v in (data.get("vat_by_rate") or {}).items()}
    pages = data.get("pages") or {}
    hidden = data.get("hidden_instructions")
    return {
        "supplier_afm": re.sub(r"\D", "", str(data.get("supplier_afm") or "")),
        "series": str(data.get("series") or "").strip(),
        "number": str(data.get("number") or "").strip(),
        "date": str(data.get("date") or ""),
        "lines": lines,
        "net_cents": _int(data.get("net_cents")) or 0,
        "vat_by_rate": vat,
        "total_cents": _int(data.get("total_cents")) or 0,
        "iban": (re.sub(r"\s", "", data["iban"]).upper() if data.get("iban") else None),
        "payment": clean_payment(data.get("payment"), str(data.get("date") or "")),
        "pages": {"seen": _int(pages.get("seen")) or 1, "total": _int(pages.get("total"))},
        "hidden_instructions": str(hidden)[:300] if hidden else None,
    }


def checks(conn, inv):
    lines_sum = sum(line["net_cents"] for line in inv["lines"])
    arithmetic = (
        abs(lines_sum - inv["net_cents"]) <= TOLERANCE_CENTS
        and abs(inv["net_cents"] + sum(inv["vat_by_rate"].values()) - inv["total_cents"])
        <= TOLERANCE_CENTS
    )
    pages = inv["pages"]
    return {
        "arithmetic": arithmetic,
        "afm_valid": afm_valid(inv["supplier_afm"]),
        "supplier_known": _supplier_by_afm(conn, inv["supplier_afm"]) is not None,
        "pages_complete": pages["total"] is None or pages["seen"] >= pages["total"],
    }


def record_read(conn, invoice_id, data, caused_by=None):
    """Write invoice.read (or invoice.duplicate_suspected). Returns (event, written_ids, duplicate)."""
    inv = clean(data)
    ids = []
    if inv["hidden_instructions"]:
        e, _ = append_event(
            conn, type="exception.flagged", actor="accountant",
            payload={"kind": "hidden_instructions", "ref": f"invoice:{invoice_id}",
                     "note": "Το τιμολόγιο έχει κρυμμένες οδηγίες. Αγνοήθηκαν."},
            subject=f"invoice:{invoice_id}", idem_key=f"hidden:{invoice_id}",
            caused_by=caused_by,
        )
        ids.append(e.id)
    supplier = _supplier_by_afm(conn, inv["supplier_afm"])
    sid = supplier["id"] if supplier else None
    lines = []
    for line in inv["lines"]:
        out = {k: line[k] for k in ("name", "qty", "unit_cents", "net_cents", "vat_rate")}
        pid = guess_product(conn, sid, line["name"])
        if pid:
            out["product_id"] = pid
        lines.append(out)
    chk = checks(conn, inv)
    payload = {
        "invoice_id": invoice_id, "supplier_afm": inv["supplier_afm"],
        "series": inv["series"], "number": inv["number"], "date": inv["date"],
        "lines": lines, "net_cents": inv["net_cents"], "vat_by_rate": inv["vat_by_rate"],
        "total_cents": inv["total_cents"], "checks": chk,
        "confidence": "high" if all(chk.values()) else "low",
        "payment": inv["payment"],
    }
    if inv["iban"]:
        payload["iban"] = inv["iban"]
    key = f"invoice:{inv['supplier_afm']}:{inv['series']}-{inv['number']}"
    event, created = append_event(
        conn, type="invoice.read", actor=ACTOR, payload=payload,
        subject=f"invoice:{invoice_id}", idem_key=key, caused_by=caused_by,
    )
    if not created and event.payload["invoice_id"] != invoice_id:
        dup, _ = append_event(
            conn, type="invoice.duplicate_suspected", actor=ACTOR,
            payload={"invoice_id": invoice_id,
                     "other_invoice_id": event.payload["invoice_id"], "rule": "same_number"},
            subject=f"invoice:{invoice_id}", idem_key=f"duplicate:{invoice_id}",
            caused_by=caused_by,
        )
        ids.append(dup.id)
        return dup, ids, True
    ids.append(event.id)
    return event, ids, False


# ---------- 3. matching ----------

def _last_delivered_order(conn, supplier_id):
    """(order_event, delivery_completed_event) of the latest delivered order of the supplier."""
    started = {}
    for e in read_events(conn, types=["delivery.started"]):
        if e.payload.get("supplier_id") == supplier_id:
            started[e.payload["delivery_id"]] = e.payload["order_id"]
    best = None
    for e in read_events(conn, types=["delivery.completed"]):
        if e.payload.get("delivery_id") in started:
            best = (started[e.payload["delivery_id"]], e)
    if best is None:
        return None, None
    order = None
    for e in read_events(conn, types=["order.proposed"]):
        if e.payload.get("order_id") == best[0]:
            order = e
    return order, best[1]


def _pname(conn, pid):
    row = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()
    return row["name"] if row else (pid or "άγνωστο είδος")


def compute_issues(conn, read_payload, order_payload, delivery_payload):
    invoiced, unit = {}, {}
    issues = []
    for line in read_payload["lines"]:
        pid = line.get("product_id")
        if not pid:
            issues.append({"kind": "not_ordered", "product_id": None, "expected": 0,
                           "actual": line.get("qty"), "amount_cents": line["net_cents"],
                           "note": f"Άγνωστο είδος στο τιμολόγιο: {line['name']}"})
            continue
        invoiced[pid] = invoiced.get(pid, 0) + (line.get("qty") or 0)
        if line.get("unit_cents") is not None:
            unit[pid] = line["unit_cents"]
    ordered = {l["product_id"]: l for l in order_payload.get("lines", [])}
    delivered = {}
    for l in delivery_payload.get("lines", []):
        delivered[l["product_id"]] = l.get("scanned", l.get("expected", 0))
    for pid, qty in invoiced.items():
        name = _pname(conn, pid)
        u = unit.get(pid) or 0
        if pid not in ordered:
            issues.append({"kind": "not_ordered", "product_id": pid, "expected": 0,
                           "actual": qty, "amount_cents": round(qty * u),
                           "note": f"{name}: τιμολογήθηκε χωρίς παραγγελία"})
            continue
        got = delivered.get(pid, 0)
        if qty > got:
            missing = qty - got
            issues.append({"kind": "shortage", "product_id": pid, "expected": qty,
                           "actual": got, "amount_cents": round(missing * u),
                           "note": f"{name}: τιμολογήθηκαν {qty:g}, ήρθαν {got:g} "
                                   f"({format_eur(round(missing * u))})"})
        elif qty != ordered[pid]["qty"]:
            diff = qty - ordered[pid]["qty"]
            issues.append({"kind": "qty_diff", "product_id": pid,
                           "expected": ordered[pid]["qty"], "actual": qty,
                           "amount_cents": round(diff * u),
                           "note": f"{name}: παραγγέλθηκαν {ordered[pid]['qty']:g}, "
                                   f"τιμολογήθηκαν {qty:g}"})
        ou = ordered[pid].get("unit_cents")
        if ou and u and abs(u - ou) * 100.0 / ou > PRICE_DIFF_PCT:
            issues.append({"kind": "price_change", "product_id": pid, "expected": ou,
                           "actual": u, "amount_cents": round((u - ou) * qty),
                           "note": f"{name}: {format_eur(ou)} στην παραγγελία, "
                                   f"{format_eur(u)} στο τιμολόγιο"})
    return issues


def match(conn, invoice_id, caused_by=None):
    """Write invoice.matched for an invoice that has been read. Returns the event."""
    read = _latest(conn, "invoice.read", invoice_id)
    supplier = _supplier_by_afm(conn, read.payload["supplier_afm"])
    order, delivery = (None, None)
    if supplier:
        order, delivery = _last_delivered_order(conn, supplier["id"])
    payload = {"invoice_id": invoice_id, "issues": []}
    if order and delivery:
        payload["order_id"] = order.payload["order_id"]
        payload["delivery_id"] = delivery.payload["delivery_id"]
        payload["issues"] = compute_issues(conn, read.payload, order.payload, delivery.payload)
    event, _ = append_event(
        conn, type="invoice.matched", actor=ACTOR, payload=payload,
        subject=f"invoice:{invoice_id}", idem_key=f"matched:{invoice_id}",
        caused_by=caused_by or read.id,
    )
    if "delivery_id" not in payload:
        stock_from_invoice(conn, read, caused_by=event.id)
    return event


def stock_from_invoice(conn, read_event, caused_by=None):
    """No delivery was scanned: each invoice line with a known product goes into stock.

    Quantities are taken as printed (product units). With a delivery the scan already
    counted the goods, so this is not called then. Returns the stock.moved events written.
    """
    p = read_event.payload
    totals = {}
    for line in p["lines"]:
        pid, qty = line.get("product_id"), line.get("qty")
        if pid and qty:
            totals[pid] = totals.get(pid, 0) + qty
    out = []
    for pid, qty in totals.items():
        e, created = append_event(
            conn, type="stock.moved", actor=ACTOR,
            payload={"product_id": pid, "qty": qty, "reason": "invoice",
                     "invoice_id": p["invoice_id"]},
            subject=f"product:{pid}", idem_key=f"invoice-stock:{p['invoice_id']}:{pid}",
            caused_by=caused_by or read_event.id,
        )
        if created:
            out.append(e)
    return out


# ---------- 4. after matching ----------

def price_changes(conn, read_event):
    """price.changed for each product >= 3% away from the previous price of this supplier."""
    p = read_event.payload
    supplier = _supplier_by_afm(conn, p["supplier_afm"])
    if not supplier:
        return []
    out = []
    for line in p["lines"]:
        pid, new = line.get("product_id"), line.get("unit_cents")
        if not pid or not new:
            continue
        old = views.last_unit_price_cents(conn, pid, p["supplier_afm"], until_id=read_event.id - 1)
        if not old:
            continue
        pct = round((new - old) * 100.0 / old, 1)
        if abs(pct) >= PRICE_CHANGE_PCT:
            e, _ = append_event(
                conn, type="price.changed", actor=ACTOR,
                payload={"product_id": pid, "supplier_id": supplier["id"],
                         "old_cents": old, "new_cents": new, "pct": pct},
                subject=f"product:{pid}", idem_key=f"price:{p['invoice_id']}:{pid}",
                caused_by=read_event.id,
            )
            out.append(e)
    return out


def new_iban(conn, read_event):
    p = read_event.payload
    iban = p.get("iban")
    supplier = _supplier_by_afm(conn, p["supplier_afm"])
    if not iban or not supplier:
        return None
    known = [re.sub(r"\s", "", k).upper() for k in json.loads(supplier["known_ibans"] or "[]")]
    if iban in known:
        return None
    e, _ = append_event(
        conn, type="supplier.new_iban_seen", actor=ACTOR,
        payload={"supplier_id": supplier["id"], "invoice_id": p["invoice_id"],
                 "iban_last4": iban[-4:]},
        subject=f"invoice:{p['invoice_id']}", idem_key=f"iban:{p['invoice_id']}",
        caused_by=read_event.id,
    )
    return e


def _mydata_afm(afm):
    """Demo: a seed AFM can be mapped to a myDATA dev AFM (MYDATA_AFM_MAP="seed=dev,...")."""
    for pair in (os.environ.get("MYDATA_AFM_MAP") or "").split(","):
        if "=" in pair:
            left, right = (x.strip() for x in pair.split("=", 1))
            if left == afm:
                return right
    return afm


def mydata_check(conn, read_event, write_error=True):
    """invoice.mydata_checked. With write_error=False (a retry) nothing is written while myDATA
    is still unreachable, so the screen does not get the same line every 5 minutes."""
    p = read_event.payload
    result = mydata.check_invoice(_mydata_afm(p["supplier_afm"]), p["series"], p["number"],
                                  p["total_cents"])
    if result["error"] and not write_error:
        return None
    payload = {"invoice_id": p["invoice_id"], "env": "dev", "found": result["found"],
               "totals_match": result["totals_match"]}
    if result["mark"]:
        payload["mark"] = result["mark"]
        payload["total_cents"] = p["total_cents"]
    if result["error"]:
        payload["error"] = result["error"]
    e, _ = append_event(
        conn, type="invoice.mydata_checked", actor=ACTOR, payload=payload,
        subject=f"invoice:{p['invoice_id']}",
        idem_key=None if result["error"] else f"mydata:{p['invoice_id']}",
        caused_by=read_event.id,
    )
    return e


def mydata_unanswered(conn):
    """Invoices whose latest myDATA check could not reach myDATA, oldest first."""
    latest = {}
    for e in read_events(conn, types=["invoice.mydata_checked"]):
        latest[e.payload["invoice_id"]] = e
    return [iid for iid, e in latest.items() if e.payload.get("error")]


def mydata_retry(conn, invoice_id):
    """Ask myDATA again ("θα ξαναδοκιμάσω"). Returns the new event, or None while it is still
    unreachable."""
    read = _latest(conn, "invoice.read", invoice_id)
    last = _latest(conn, "invoice.mydata_checked", invoice_id)
    if read is None or last is None or not last.payload.get("error"):
        return None
    return mydata_check(conn, read, write_error=False)


def flag_unread(conn, invoice_id, offline):
    """One "Προσοχή" for the owner when an invoice cannot be read. Once per invoice and reason."""
    received = _latest(conn, "invoice.received", invoice_id)
    if received is None:
        return None
    from xangx.master.tools import greek_time

    when = greek_time(received.at)[-5:]
    if offline:
        kind = "invoice_unread_offline"
        note = (f"Δεν μπόρεσα να διαβάσω το τιμολόγιο που ήρθε στις {when}: δεν υπάρχει σύνδεση "
                f"στο internet. Θα ξαναδοκιμάζω μόνος μου κάθε 5 λεπτά.")
    else:
        kind = "invoice_unread"
        note = (f"Δεν μπόρεσα να διαβάσω το τιμολόγιο που ήρθε στις {when}. Στείλε μου μια πιο "
                f"καθαρή φωτογραφία ή το PDF.")
    e, _ = append_event(
        conn, type="exception.flagged", actor="accountant",
        payload={"kind": kind, "ref": f"invoice:{invoice_id}", "note": note},
        subject=f"invoice:{invoice_id}", idem_key=f"{kind}:{invoice_id}", caused_by=received.id,
    )
    return e


# ---------- the whole flow ----------

def summary(conn, invoice_id):
    """One Greek sentence for the owner."""
    read = _latest(conn, "invoice.read", invoice_id)
    if read is None:
        dup = _latest(conn, "invoice.duplicate_suspected", invoice_id)
        if dup:
            return f"Το τιμολόγιο {invoice_id} μοιάζει διπλό με το {dup.payload['other_invoice_id']}."
        return f"Το τιμολόγιο {invoice_id} δεν έχει διαβαστεί ακόμα."
    p = read.payload
    supplier = _supplier_by_afm(conn, p["supplier_afm"])
    head = f"Τιμολόγιο {p['series']}-{p['number']}"
    if supplier:
        head += f" ({supplier['name']})"
    parts = [format_eur(p["total_cents"])]
    matched = _latest(conn, "invoice.matched", invoice_id)
    if matched and matched.payload.get("delivery_id"):
        issues = matched.payload["issues"]
        parts.append("ταιριάζει με την παραλαβή" if not issues
                     else "; ".join(i["note"] for i in issues))
    for e in read_events(conn, types=["price.changed"]):
        if e.caused_by == read.id:
            sign = "+" if e.payload["pct"] > 0 else ""
            parts.append(f"{_pname(conn, e.payload['product_id'])} {sign}"
                         f"{e.payload['pct']:.1f}".replace(".", ",") + "%")
    if _latest(conn, "supplier.new_iban_seen", invoice_id):
        parts.append("ΝΕΟ IBAN, θέλει επιβεβαίωση στο γνωστό τηλέφωνο")
    if not all(p["checks"].values()):
        bad = [k for k, v in p["checks"].items() if not v]
        parts.append("έλεγχοι που απέτυχαν: " + ", ".join(bad))
    md = _latest(conn, "invoice.mydata_checked", invoice_id)
    if md:
        if md.payload.get("error"):
            parts.append("myDATA: θα ξαναδοκιμάσω")
        elif md.payload["found"]:
            parts.append("βρέθηκε στο myDATA")
        else:
            parts.append("δεν βρέθηκε στο myDATA")
    return f"{head}: " + ", ".join(parts) + "."


def is_done(conn, invoice_id):
    return (_latest(conn, "invoice.matched", invoice_id) is not None
            or _latest(conn, "invoice.duplicate_suspected", invoice_id) is not None)


def open_invoices(conn):
    """Invoices received but not processed, or processed with issues."""
    out = []
    for e in read_events(conn, types=["invoice.received"]):
        iid = e.payload["invoice_id"]
        if not is_done(conn, iid):
            out.append({"invoice_id": iid, "state": "not_processed"})
            continue
        m = _latest(conn, "invoice.matched", iid)
        read = _latest(conn, "invoice.read", iid)
        bad_checks = read is not None and not all(read.payload["checks"].values())
        if (m and m.payload["issues"]) or bad_checks or _latest(
                conn, "supplier.new_iban_seen", iid):
            out.append({"invoice_id": iid, "state": "has_issues"})
    return out


def process_invoice(conn, invoice_id, reader=read_invoice):
    """Read, check, match, prices, IBAN, myDATA. Runs once per invoice.

    Returns {"summary", "event_ids", "already_done"}.
    """
    if is_done(conn, invoice_id):
        return {"summary": summary(conn, invoice_id), "event_ids": [], "already_done": True}
    received = _latest(conn, "invoice.received", invoice_id)
    if received is None:
        return {"summary": f"Δεν υπάρχει τιμολόγιο {invoice_id}.", "event_ids": [],
                "already_done": False}
    ids = []
    read = _latest(conn, "invoice.read", invoice_id)
    if read is None:
        data = reader(data_dir() / received.payload["file_ref"])
        read, written, duplicate = record_read(conn, invoice_id, data, caused_by=received.id)
        ids += written
        if duplicate:
            return {"summary": summary(conn, invoice_id), "event_ids": ids,
                    "already_done": False}
    m = match(conn, invoice_id, caused_by=read.id)
    ids.append(m.id)
    ids += [e.id for e in price_changes(conn, read)]
    iban_event = new_iban(conn, read)
    if iban_event:
        ids.append(iban_event.id)
    ids.append(mydata_check(conn, read).id)
    return {"summary": summary(conn, invoice_id), "event_ids": ids, "already_done": False}
