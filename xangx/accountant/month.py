"""The month's folder for the accountant: a ZIP with the invoice files and a summary.

Nothing here changes anything outside the event log and data/files/. Money is in cents.
"""

import csv
import hashlib
import html
import io
import re
import zipfile
from datetime import date

from xangx.invoices import core as invoices
from xangx.shared import append_event, read_events
from xangx.shared.money import format_eur

ACTOR = "accountant"
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
MONTHS = ["Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος",
          "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος"]
METHODS = {"cash": "μετρητά", "card": "κάρτα", "bank": "τράπεζα"}
ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # fixed, so the same content gives the same file


def valid_month(month):
    return bool(isinstance(month, str) and MONTH_RE.match(month))


def month_label(month):
    y, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def _cents(value):
    """12345 -> '123,45' (comma, no thousands mark: Excel in Greek reads it as a number)."""
    sign = "-" if value < 0 else ""
    euros, rest = divmod(abs(int(value)), 100)
    return f"{sign}{euros},{rest:02d}"


def _gr_date(iso):
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or ""


def _safe(text):
    """A cell that Excel must not run as a formula."""
    text = str(text if text is not None else "")
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def collect(conn, month):
    """One dict per invoice of the month, oldest first, plus the totals."""
    suppliers = {r["afm"]: r["name"] for r in conn.execute("SELECT afm, name FROM suppliers")}
    received, reads, matched, mydata = {}, {}, {}, {}
    reminders, asked, recorded, credits, flags = {}, {}, {}, {}, {}
    wanted = {
        "invoice.received": received, "invoice.read": reads, "invoice.matched": matched,
        "invoice.mydata_checked": mydata, "payment.reminder_set": reminders,
        "payment.asked": asked, "payment.recorded": recorded, "credit.requested": credits,
    }
    for e in read_events(conn, types=list(wanted) + ["exception.flagged"]):
        if e.type == "exception.flagged":
            ref = str(e.payload.get("ref", ""))
            if ref.startswith("invoice:"):
                flags.setdefault(ref[8:], []).append(e.payload.get("note", ""))
            continue
        if e.type == "invoice.read" and e.actor != "invoices":
            continue
        iid = e.payload.get("invoice_id")
        if iid:
            wanted[e.type][iid] = e  # the latest one wins
    items = []
    for iid, read in reads.items():
        p = read.payload
        if not str(p.get("date", "")).startswith(month + "-"):
            continue
        paid = recorded.get(iid)
        method = paid.payload.get("method") if paid else None
        if method in METHODS:
            pay = f"Πληρώθηκε με {METHODS[method]}"
            unpaid = False
        elif iid in reminders:
            pay = f"Υπενθύμιση, λήξη {_gr_date(reminders[iid].payload.get('due_on'))}"
            unpaid = True
        else:
            pay = "Εκκρεμεί"
            unpaid = True
        md = mydata.get(iid)
        if md is None:
            md_text = "Δεν ελέγχθηκε"
        else:
            md_text = "Βρέθηκε" if md.payload.get("found") else "Δεν βρέθηκε"
        issues = []
        if iid in matched:
            issues += [str(i.get("note", i.get("kind", ""))) for i in matched[iid].payload.get("issues", [])]
        issues += flags.get(iid, [])
        credit = credits[iid].payload["amount_cents"] if iid in credits else 0
        rec = received.get(iid)
        items.append({
            "invoice_id": iid, "number": f"{p.get('series', '')}-{p.get('number', '')}",
            "supplier": suppliers.get(p.get("supplier_afm")) or p.get("supplier_afm") or "",
            "date": p["date"], "net_cents": p["net_cents"],
            "vat": {str(k): int(v) for k, v in (p.get("vat_by_rate") or {}).items()},
            "total_cents": p["total_cents"], "mydata": md_text, "payment": pay,
            "unpaid": unpaid, "issues": issues, "credit_cents": credit,
            "iban_last4": (p.get("iban") or "")[-4:],
            "file_ref": rec.payload["file_ref"] if rec else None,
        })
    items.sort(key=lambda i: (i["date"], i["number"], i["invoice_id"]))
    rates = sorted({r for i in items for r in i["vat"]}, key=int, reverse=True)
    vat_total = {r: sum(i["vat"].get(r, 0) for i in items) for r in rates}
    return {"month": month, "items": items, "rates": rates, "vat_total": vat_total,
            "total_cents": sum(i["total_cents"] for i in items),
            "net_cents": sum(i["net_cents"] for i in items),
            "vat_cents": sum(vat_total.values()),
            "unpaid": sum(1 for i in items if i["unpaid"])}


def summary_text(data):
    n = len(data["items"])
    label = month_label(data["month"])
    if n == 0:
        return f"{label}: κανένα τιμολόγιο."
    unpaid = data["unpaid"]
    if unpaid == 0:
        pending = "καμία εκκρεμής πληρωμή"
    elif unpaid == 1:
        pending = "1 εκκρεμεί πληρωμή"
    else:
        pending = f"{unpaid} εκκρεμούν πληρωμές"
    word = "τιμολόγιο" if n == 1 else "τιμολόγια"
    return (f"{label}: {n} {word}, {format_eur(data['total_cents'])}, "
            f"ΦΠΑ {format_eur(data['vat_cents'])}, {pending}")


def _headers(data):
    return (["Αριθμός", "Προμηθευτής", "Ημερομηνία", "Καθαρό"]
            + [f"ΦΠΑ {r}%" for r in data["rates"]]
            + ["Σύνολο", "myDATA", "Πληρωμή", "IBAN (τελευταία 4)", "Αποκλίσεις", "Πιστωτικό"])


def _row(data, i, fmt, text):
    return ([text(i["number"]), text(i["supplier"]), _gr_date(i["date"]), fmt(i["net_cents"])]
            + [fmt(i["vat"].get(r, 0)) for r in data["rates"]]
            + [fmt(i["total_cents"]), i["mydata"], i["payment"],
               text(i["iban_last4"]), text(" | ".join(i["issues"])),
               fmt(i["credit_cents"]) if i["credit_cents"] else ""])


def make_csv(data):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=";")
    w.writerow(_headers(data))
    for i in data["items"]:
        w.writerow(_row(data, i, _cents, _safe))
    w.writerow(["Σύνολο", "", "", _cents(data["net_cents"])]
               + [_cents(data["vat_total"][r]) for r in data["rates"]]
               + [_cents(data["total_cents"]), "", "", "", "", ""])
    return buf.getvalue().encode("utf-8-sig")


def make_html(data):
    esc = html.escape
    head = "".join(f"<th>{esc(h)}</th>" for h in _headers(data))
    rows = "".join(
        "<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in _row(data, i, format_eur, str)) + "</tr>"
        for i in data["items"])
    total = (f"<tr class=\"t\"><td>Σύνολο</td><td></td><td></td><td>{esc(format_eur(data['net_cents']))}</td>"
             + "".join(f"<td>{esc(format_eur(data['vat_total'][r]))}</td>" for r in data["rates"])
             + f"<td>{esc(format_eur(data['total_cents']))}</td><td></td><td></td><td></td><td></td><td></td></tr>")
    title = esc(f"Φάκελος του μήνα: {month_label(data['month'])}")
    return (
        "<!doctype html>\n<html lang=\"el\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>"
        "body{font-family:Arial,sans-serif;margin:24px;color:#111}"
        "table{border-collapse:collapse;width:100%;font-size:13px}"
        "th,td{border:1px solid #999;padding:4px 6px;text-align:left}"
        "th{background:#eee}.t td{font-weight:bold}"
        "@media print{body{margin:8mm}}</style></head><body>"
        f"<h1>{title}</h1><p>{esc(summary_text(data))}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}{total}</tbody></table>"
        "<p>Τα φορολογικά είναι πληροφορία υποστήριξης, όχι συμβουλή λογιστή.</p>"
        "</body></html>\n").encode("utf-8")


def make_zip(data):
    """ZIP bytes: synopsi.csv, synopsi.html and the invoice files that are on disk."""
    base = invoices.data_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name, content):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
        add("synopsi.csv", make_csv(data))
        add("synopsi.html", make_html(data))
        for i in data["items"]:
            ref = i["file_ref"]
            if not ref:
                continue
            path = base / ref
            if path.is_file() and path.resolve().is_relative_to((base / "files").resolve()):
                safe = re.sub(r"[^\w.-]+", "_", i["number"])
                add(f"timologia/{safe}_{i['invoice_id']}{path.suffix}", path.read_bytes())
    return buf.getvalue()


def build(conn, month):
    """Make (or find again) the month's ZIP and write report.month_created.

    Returns (data, file_ref, sha256, event_or_None). The same content gives the same file.
    """
    data = collect(conn, month)
    content = make_zip(data)
    sha = hashlib.sha256(content).hexdigest()
    files = invoices.data_dir() / "files"
    files.mkdir(parents=True, exist_ok=True)
    target = files / f"{sha}.zip"
    if not target.exists():
        target.write_bytes(content)
    file_ref = f"files/{sha}.zip"
    event, created = append_event(
        conn, type="report.month_created", actor=ACTOR,
        payload={"month": month, "invoice_count": len(data["items"]),
                 "total_cents": data["total_cents"], "file_ref": file_ref},
        subject=f"report:{month}", idem_key=f"month:{month}:{sha}",
    )
    return data, file_ref, sha, (event if created else None)
