"""Data for the Tasks and Invoices pages: everything comes from the events.

Included by the server with `app.include_router(router)`. Routes: /api/screen/tasks,
/api/screen/invoices and /api/screen/invoices/{invoice_id}. Read only. A whole IBAN is never
sent: only its last four characters.
"""

import re
from datetime import datetime, timezone

import fastapi
from fastapi import HTTPException

from xangx.accountant import core as accountant
from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.describe import _mask, describe, load_names
from xangx.shared import undo, views
from xangx.shared.db import connect, init_db
from xangx.shared.events import read_events
from xangx.shared.money import format_eur

router = fastapi.APIRouter()

# Same list as the home screen: what counts as "a job done".
TASK_TYPES = (
    "check.morning_done", "stock.low_detected", "order.proposed", "order.sent",
    "order.reply_received", "delivery.completed", "invoice.read", "invoice.matched",
    "invoice.mydata_checked", "price.changed", "supplier.new_iban_seen",
    "payment.reminder_set", "credit.requested", "report.daily_created",
    "exception.flagged", "agent.woke",
)
# key, name, actors. Same mapping as the home screen; the partner's own jobs come last.
TEAMS = (
    ("orders", "Παραγγελίες", ("orders",)),
    ("stock", "Αποθήκη", ("camera", "stock", "scanner")),
    ("invoices", "Τιμολόγια", ("invoices",)),
    ("accountant", "Λογιστής", ("accountant",)),
    ("master", "Συνεργάτης", ("master",)),
    ("agent", "Άλλος agent", ()),
)
TEAM_OF = {actor: (key, name) for key, name, actors in TEAMS for actor in actors}
# Seeded and system events have the actor "system": the type tells which team the job belongs to.
TEAM_BY_PREFIX = (
    ("order.", "orders"), ("stock.", "stock"), ("shelf.", "stock"), ("delivery.", "stock"),
    ("invoice.", "invoices"), ("price.", "invoices"), ("supplier.", "invoices"),
    ("credit.", "invoices"), ("payment.", "accountant"), ("report.", "accountant"),
    ("exception.", "accountant"),
)
TEAM_NAME = {key: name for key, name, _ in TEAMS}


def _is_mcp_call(event):
    return event.type == "agent.planned" and (event.payload or {}).get("via") == "mcp"


def _is_task(event):
    return event.type in TASK_TYPES or _is_mcp_call(event)


def _team(event):
    if _is_mcp_call(event):
        return "agent", TEAM_NAME["agent"]
    if event.actor in TEAM_OF:
        return TEAM_OF[event.actor]
    for prefix, key in TEAM_BY_PREFIX:
        if event.type.startswith(prefix):
            return key, TEAM_NAME[key]
    return "master", TEAM_NAME["master"]
WEEKDAYS = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή")
INVOICE_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
INVOICE_TYPES = (
    "invoice.received", "invoice.read", "invoice.duplicate_suspected", "invoice.matched",
    "invoice.mydata_checked", "price.changed", "supplier.new_iban_seen", "credit.requested",
    "payment.reminder_set", "exception.flagged",
)


def _db():
    conn = connect()
    init_db(conn)
    return conn


def _parse(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _local(at):
    moment = _parse(at)
    return moment + greek_offset(moment)


def _hhmm(at):
    return _local(at).strftime("%H:%M")


def _ddmmyyyy(iso_day):
    try:
        return datetime.strptime(str(iso_day)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(iso_day or "")


def _pct(value):
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.1f}".replace(".", ",") + "%"


def _reference(conn, until_id):
    """"Now": the real clock, or the last event up to `until_id` (time machine)."""
    if until_id is not None:
        row = conn.execute(
            "SELECT at FROM events WHERE id <= ? ORDER BY id DESC LIMIT 1", (until_id,)
        ).fetchone()
        if row:
            return _parse(row["at"])
    return datetime.now(timezone.utc)


def _clock(conn, until_id):
    now = _reference(conn, until_id)
    return (now + greek_offset(now)).strftime("%H:%M")


def _since(conn, day_start, until_id, types):
    row = conn.execute("SELECT MIN(id) AS m FROM events WHERE at >= ?", (day_start,)).fetchone()
    if row["m"] is None:
        return []
    return [e for e in read_events(conn, after_id=row["m"] - 1, until_id=until_id, types=types)
            if e.at >= day_start]


def _last_id(conn, until_id):
    sql = "SELECT COALESCE(MAX(id),0) AS m FROM events"
    if until_id is None:
        return conn.execute(sql).fetchone()["m"]
    return conn.execute(sql + " WHERE id <= ?", (until_id,)).fetchone()["m"]


# ---------- invoices: everything the events say about each one ----------

def _bundles(conn, until_id):
    """invoice_id -> {"received", "read", "dup", "matched", "mydata", "iban", "credit",
    "reminder", "flags", "prices"} from the events (latest of each kind)."""
    events = read_events(conn, until_id=until_id, types=INVOICE_TYPES)
    read_owner = {e.id: e.payload.get("invoice_id") for e in events if e.type == "invoice.read"}
    out = {}

    def slot(iid):
        return out.setdefault(iid, {"flags": [], "prices": []})

    for e in events:
        p = e.payload
        iid = p.get("invoice_id")
        if e.type == "price.changed":
            iid = read_owner.get(e.caused_by)
        elif e.type == "exception.flagged":
            ref = str(p.get("ref") or "")
            iid = ref[len("invoice:"):] if ref.startswith("invoice:") else None
        if not iid:
            continue
        b = slot(iid)
        key = {"invoice.received": "received", "invoice.read": "read",
               "invoice.duplicate_suspected": "dup", "invoice.matched": "matched",
               "invoice.mydata_checked": "mydata", "supplier.new_iban_seen": "iban",
               "credit.requested": "credit", "payment.reminder_set": "reminder"}.get(e.type)
        if key:
            b[key] = e
        elif e.type == "price.changed":
            b["prices"].append(e)
        elif e.type == "exception.flagged":
            b["flags"].append(e)
    return out


def _label(bundle, invoice_id):
    read = bundle.get("read")
    if read:
        return f"{read.payload.get('series') or ''}-{read.payload.get('number') or ''}".strip("-")
    return invoice_id


def _via(received):
    """How the paper arrived: "email", "photo", or None (a file in the inbox, or unknown)."""
    source = ((received.payload or {}).get("source") if received else None)
    return source if source in ("email", "photo") else None


def _supplier_name(conn, afm):
    row = conn.execute("SELECT name FROM suppliers WHERE afm = ?", (afm,)).fetchone()
    return row["name"] if row else "Άγνωστος προμηθευτής"


def _pending_credit(conn, invoice_id, until_id):
    for e in views.pending_approvals(conn, until_id=until_id):
        p = e.payload
        args = p.get("args") or {}
        if p.get("action") == "request_credit" and (
                args.get("invoice_id") == invoice_id or p.get("ref") == f"invoice:{invoice_id}"):
            return e
    return None


def _status(conn, iid, b, pending):
    """(level, one line) for the list: ok, warn or wait."""
    items = []
    if b.get("dup"):
        other = b["dup"].payload.get("other_invoice_id")
        return "warn", f"Μοιάζει διπλό με το {load_names(conn)['invoice'].get(other) or other}"
    read = b.get("read")
    if read is None:
        return "wait", "Δεν έχει διαβαστεί ακόμα"
    if pending is not None:
        items.append("Θέλει το «ναι» σου για πιστωτικό")
    elif b.get("credit"):
        items.append("Ζητήθηκε πιστωτικό " + format_eur(b["credit"].payload["amount_cents"]))
    matched = b.get("matched")
    if matched and pending is None and not b.get("credit"):
        for issue in matched.payload.get("issues") or []:
            if issue.get("kind") in ("shortage", "qty_diff", "not_ordered"):
                items.append(issue.get("note") or "Απόκλιση στις ποσότητες")
                break
    if b.get("iban"):
        items.append(f"Νέο IBAN, λήγει σε …{b['iban'].payload.get('iban_last4')}")
    names = load_names(conn)["product"]
    for e in b["prices"]:
        items.append(f"{names.get(e.payload.get('product_id')) or 'Είδος'} {_pct(e.payload['pct'])}")
    if any((f.payload or {}).get("kind") == "hidden_instructions" for f in b["flags"]):
        items.append("Κρυμμένες οδηγίες, αγνοήθηκαν")
    mydata = b.get("mydata")
    if mydata and not mydata.payload.get("error") and not (
            mydata.payload.get("found") and mydata.payload.get("totals_match")):
        items.append("myDATA: δεν ταιριάζει")
    if not all((read.payload.get("checks") or {}).values()):
        items.append("Απέτυχε έλεγχος")
    if items:
        return "warn", " · ".join(items[:2])
    return "ok", "Όλα σωστά"


def _list_item(conn, iid, b, pending):
    read = b.get("read")
    level, status = _status(conn, iid, b, pending)
    first = b.get("received") or read
    item = {
        "invoice_id": iid,
        "label": _label(b, iid),
        "supplier": _supplier_name(conn, read.payload.get("supplier_afm")) if read else None,
        "at": _hhmm(first.at) if first else None,
        "level": level,
        "status": status,
        "pending_credit": pending is not None,
    }
    if read:
        item["total_cents"] = read.payload.get("total_cents")
        item["total_text"] = format_eur(read.payload.get("total_cents") or 0)
    return item


@router.get("/api/screen/invoices")
def invoices_today(until_id: int | None = None):
    conn = _db()
    try:
        day_start = greek_day_start_utc(_reference(conn, until_id))
        out = []
        for iid, b in _bundles(conn, until_id).items():
            first = b.get("received") or b.get("read") or b.get("dup")
            if first is None or first.at < day_start:
                continue
            out.append((first.id, _list_item(conn, iid, b, _pending_credit(conn, iid, until_id))))
        out.sort(key=lambda pair: -pair[0])
        return {"invoices": [item for _, item in out], "last_id": _last_id(conn, until_id),
                "paused": views.is_paused(conn, until_id=until_id), "now": _clock(conn, until_id)}
    finally:
        conn.close()


def _lines(conn, read, matched, prices, until_id):
    """Table lines: ordered / delivered / charged, when the matching knows the order."""
    ordered = delivered = None
    if matched and matched.payload.get("order_id"):
        for e in read_events(conn, until_id=until_id, types=["order.proposed"]):
            if e.payload.get("order_id") == matched.payload["order_id"]:
                ordered = {l["product_id"]: l.get("qty") for l in e.payload.get("lines", [])}
    if matched and matched.payload.get("delivery_id"):
        for e in read_events(conn, until_id=until_id, types=["delivery.completed"]):
            if e.payload.get("delivery_id") == matched.payload["delivery_id"]:
                delivered = {l["product_id"]: l.get("scanned", l.get("expected", 0))
                             for l in e.payload.get("lines", [])}
    bad = {i.get("product_id") for i in (matched.payload.get("issues") if matched else []) or []
           if i.get("kind") in ("shortage", "qty_diff", "not_ordered")}
    old = {e.payload["product_id"]: e.payload for e in prices}
    lines = []
    for l in read.payload.get("lines", []):
        pid = l.get("product_id")
        line = {"name": l.get("name"), "charged": l.get("qty"), "unit_cents": l.get("unit_cents"),
                "unit_text": format_eur(l["unit_cents"]) if l.get("unit_cents") is not None else None,
                "ordered": None, "delivered": None, "diff": pid in bad if pid else False}
        if ordered is not None:
            line["ordered"] = ordered.get(pid, 0)
        if delivered is not None:
            line["delivered"] = delivered.get(pid, 0)
        change = old.get(pid)
        if change:
            line["price_pct"] = change["pct"]
            line["price_pct_text"] = _pct(change["pct"])
            line["old_unit_text"] = format_eur(change["old_cents"])
        lines.append(line)
    return lines


def _checks(b, names):
    """The rows under the table: rule-based checks, each with ok true/false."""
    out = []
    read = b["read"]
    chk = read.payload.get("checks") or {}
    if "arithmetic" in chk:
        out.append({"ok": bool(chk["arithmetic"]),
                    "text": "Ποσά και ΦΠΑ σωστά" if chk["arithmetic"]
                    else "Τα ποσά ή το ΦΠΑ δεν βγαίνουν"})
    if "afm_valid" in chk or "supplier_known" in chk:
        if not chk.get("afm_valid", True):
            out.append({"ok": False, "text": "Το ΑΦΜ του προμηθευτή δεν είναι έγκυρο"})
        elif chk.get("supplier_known", True):
            out.append({"ok": True, "text": "ΑΦΜ προμηθευτή γνωστό"})
        else:
            out.append({"ok": False, "text": "ΑΦΜ προμηθευτή άγνωστο"})
    if chk.get("pages_complete") is False:
        out.append({"ok": False, "text": "Λείπουν σελίδες του τιμολογίου"})
    mydata = b.get("mydata")
    if mydata:
        m = mydata.payload
        if m.get("error"):
            out.append({"ok": False, "text": "myDATA: δεν απάντησε, θα ξαναδοκιμάσω"})
        elif m.get("found") and m.get("totals_match"):
            out.append({"ok": True, "text": "myDATA: βρέθηκε, ίδιο σύνολο"})
        elif m.get("found"):
            out.append({"ok": False, "text": "myDATA: βρέθηκε, αλλά με άλλο σύνολο"})
        else:
            out.append({"ok": False, "text": "myDATA: δεν βρέθηκε"})
    if b.get("iban"):
        out.append({"ok": False, "lead": "Νέο IBAN.",
                    "text": f"Λήγει σε …{b['iban'].payload.get('iban_last4')}. Δεν το αλλάζω χωρίς εσένα."})
    elif read.payload.get("iban"):
        out.append({"ok": True, "text": "IBAN γνωστό"})
    for e in b["prices"]:
        p = e.payload
        name = names["product"].get(p.get("product_id")) or "Είδος"
        out.append({"ok": False, "lead": f"{name} {_pct(p['pct'])}.",
                    "text": f"Ήταν {format_eur(p['old_cents'])}, τώρα {format_eur(p['new_cents'])}."})
    for e in b["flags"]:
        out.append({"ok": False, "text": _mask(str(e.payload.get("note") or ""))})
    if b.get("reminder"):
        out.append({"ok": True, "text": "Υπενθύμιση πληρωμής " + _ddmmyyyy(b["reminder"].payload.get("due_on"))})
    if b.get("credit"):
        out.append({"ok": True, "text": "Ζητήθηκε πιστωτικό "
                    + format_eur(b["credit"].payload.get("amount_cents") or 0)})
    return out


def _credit_scene(conn, iid, pending):
    """The owner's question about a credit note, with the amount the email would ask for."""
    scene = {"approval_id": pending.payload.get("approval_id"),
             "at": _hhmm(pending.at), "why": pending.payload.get("why") or ""}
    try:
        d = accountant.credit_details(conn, iid)
    except Exception:
        d = None
    if d:
        scene["amount_cents"] = d["amount_cents"]
        scene["amount_text"] = format_eur(d["amount_cents"])
        scene["supplier"] = d["supplier"]["name"]
    else:
        scene["supplier"] = None
        scene["summary"] = _mask(str(pending.payload.get("summary") or ""))
    return scene


@router.get("/api/screen/invoices/{invoice_id}")
def invoice_detail(invoice_id: str, until_id: int | None = None):
    if not INVOICE_ID.match(invoice_id):
        raise HTTPException(404, "Δεν βρήκα αυτό το τιμολόγιο.")
    conn = _db()
    try:
        bundles = _bundles(conn, until_id)
        b = bundles.get(invoice_id)
        if b is None or not (b.get("received") or b.get("read") or b.get("dup")):
            raise HTTPException(404, "Δεν βρήκα αυτό το τιμολόγιο.")
        names = load_names(conn)
        pending = _pending_credit(conn, invoice_id, until_id)
        item = _list_item(conn, invoice_id, b, pending)
        from xangx.screen.api_tim import invoice_extras  # lazy: api_tim imports this module
        extras = invoice_extras(conn, invoice_id, until_id, bundles, pending)
        out = {"invoice_id": invoice_id, "label": item["label"], "supplier": item["supplier"],
               "received_at": item["at"], "status": item["status"], "level": item["level"],
               "read": False, "lines": [], "checks": [], "credit": None,
               **extras,
               "last_id": _last_id(conn, until_id),
               "paused": views.is_paused(conn, until_id=until_id), "now": _clock(conn, until_id)}
        if b.get("dup"):
            out["duplicate_of"] = b["dup"].payload.get("other_invoice_id")
        read = b.get("read")
        if read is None:
            return out
        p = read.payload
        out["read"] = True
        out["date"] = _ddmmyyyy(p.get("date"))
        out["order_id"] = (b["matched"].payload.get("order_id") if b.get("matched") else None)
        net, total = p.get("net_cents") or 0, p.get("total_cents") or 0
        out["net_text"], out["vat_text"], out["total_text"] = (
            format_eur(net), format_eur(total - net), format_eur(total))
        out["lines"] = _lines(conn, read, b.get("matched"), b["prices"], until_id)
        out["has_order"] = any(l["ordered"] is not None for l in out["lines"])
        out["has_delivery"] = any(l["delivered"] is not None for l in out["lines"])
        out["checks"] = _checks(b, names)
        if pending is not None:
            out["credit"] = _credit_scene(conn, invoice_id, pending)
        return out
    finally:
        conn.close()


# ---------- tasks ----------

def _task_how(events):
    """ids of the events that came out of an approval the owner answered yes to, and how many yes."""
    yes = {e.id for e in events
           if e.type == "approval.decided" and e.payload.get("decision") == "yes"}
    made = {e.id for e in events if e.caused_by in yes}
    for e in events:
        if e.type == "agent.tool_ran" and e.caused_by in yes:
            made.update(i for i in e.payload.get("result_event_ids") or [] if isinstance(i, int))
    return made, len(yes)


@router.get("/api/screen/tasks")
def tasks_today(until_id: int | None = None):
    conn = _db()
    try:
        now = _reference(conn, until_id)
        day_start = greek_day_start_utc(now)
        events = _since(conn, day_start, until_id, TASK_TYPES + (
            "approval.decided", "agent.tool_ran", "agent.planned"))
        undo_index = undo.Index(conn, until_id)
        names = load_names(conn)
        bundles = _bundles(conn, until_id)
        labels = {iid: _label(b, iid) for iid, b in bundles.items()}
        owner_of = {b["read"].id: iid for iid, b in bundles.items() if b.get("read")}
        known = {iid for iid, b in bundles.items() if b.get("received") or b.get("read")}
        made_by_yes, with_yes = _task_how(events)

        groups = {}
        done = 0
        for e in events:
            if not _is_task(e):
                continue
            done += 1
            key, name = _team(e)
            p = e.payload
            iid = p.get("invoice_id") or owner_of.get(e.caused_by)
            ref = str(p.get("ref") or "")
            if not iid and ref.startswith("invoice:"):
                iid = ref[len("invoice:"):]
            text = describe(e, names)
            for known_id, label in labels.items():
                text = text.replace(known_id, label)
            groups.setdefault(key, {"key": key, "name": name, "tasks": []})["tasks"].append({
                "id": e.id, "at": _hhmm(e.at), "team": key, "text": text,
                "how": "yes" if e.id in made_by_yes else "auto",
                "undo": undo_index.state(e, now),
                "links": {"invoice_id": iid} if iid in known else {},
                "warn": e.type == "exception.flagged"
                or (e.type == "invoice.matched" and bool(p.get("issues"))),
            })
        out_groups = []
        for g in groups.values():
            g["tasks"].sort(key=lambda t: -t["id"])
            g["count"] = len(g["tasks"])
            out_groups.append(g)
        out_groups.sort(key=lambda g: -g["tasks"][0]["id"])
        errors = sum(1 for e in events
                     if e.type == "agent.tool_ran" and not e.payload.get("ok"))
        local = now + greek_offset(now)
        return {
            "day": {"date": local.strftime("%d/%m/%Y"), "weekday": WEEKDAYS[local.weekday()],
                    "now": local.strftime("%H:%M")},
            "totals": {"done": done, "with_yes": with_yes, "errors": errors},
            "groups": out_groups,
            "last_id": _last_id(conn, until_id),
            "paused": views.is_paused(conn, until_id=until_id),
        }
    finally:
        conn.close()
