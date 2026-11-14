"""Minimal myDATA (AADE) client for the test environment. Standard library only.

Two accounts can be configured in .env:
- "store":    MYDATA_USER_ID / MYDATA_SUBSCRIPTION_KEY / MYDATA_AFM
- "supplier": MYDATA_SUPPLIER_USER_ID / MYDATA_SUPPLIER_SUBSCRIPTION_KEY / MYDATA_SUPPLIER_AFM

Keys are read from the environment and never printed or logged.
Only the methods we need are called: RequestDocs, RequestTransmittedDocs, SendInvoices.
"""

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

BASE_URLS = {
    "dev": "https://mydataapidev.aade.gr/",
}
TIMEOUT_S = 10
TOTAL_TOLERANCE_CENTS = 5  # ±0,05 € on totals (CONCEPT §4)

NS_INV = "http://www.aade.gr/myDATA/invoice/v1.0"
# The "Classificaton" spelling is AADE's own.
NS_ICLS = "https://www.aade.gr/myDATA/incomeClassificaton/v1.0"
NS_ECLS = "https://www.aade.gr/myDATA/expensesClassificaton/v1.0"

# VAT rate (%) -> myDATA vatCategory
VAT_CATEGORY = {24: 1, 13: 2, 6: 3, 17: 4, 9: 5, 4: 6, 0: 7}

_PROFILES = {
    "store": ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY", "MYDATA_AFM"),
    "supplier": ("MYDATA_SUPPLIER_USER_ID", "MYDATA_SUPPLIER_SUBSCRIPTION_KEY", "MYDATA_SUPPLIER_AFM"),
}


class MyDataError(Exception):
    """Network, HTTP or configuration problem talking to myDATA."""


def credentials(profile="store"):
    """Return (user_id, key, afm) for a profile, or raise MyDataError if missing."""
    names = _PROFILES[profile]
    values = [os.environ.get(n, "").strip() for n in names]
    missing = [n for n, v in zip(names[:2], values[:2]) if not v]
    if missing:
        raise MyDataError("missing in .env: " + ", ".join(missing))
    return values[0], values[1], values[2]


def is_configured(profile):
    try:
        credentials(profile)
        return True
    except MyDataError:
        return False


def _call(profile, method, path, params=None, body=None):
    """Do one HTTP call. Returns (status, text). Never includes the key in errors."""
    user, key, _ = credentials(profile)
    env = os.environ.get("MYDATA_ENV", "dev")
    if env not in BASE_URLS:
        raise MyDataError("only the dev environment is allowed, got " + env)
    url = BASE_URLS[env] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    # user names may be Greek: send them as UTF-8 bytes
    req.add_header("aade-user-id", user.encode("utf-8").decode("latin-1"))
    req.add_header("ocp-apim-subscription-key", key)
    if data is not None:
        req.add_header("Content-Type", "application/xml")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MyDataError("myDATA unreachable: " + type(exc).__name__) from None


# ---------- reading ----------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _find(elem, name):
    for child in elem.iter():
        if _local(child.tag) == name:
            return child
    return None


def _text(elem, name):
    found = _find(elem, name)
    return found.text.strip() if found is not None and found.text else None


def _to_cents(value):
    if value is None:
        return None
    return int(round(float(value) * 100))


def parse_docs(xml_text):
    """Turn a RequestedDoc response into a list of small invoice dicts."""
    root = ET.fromstring(xml_text)
    out = []
    for inv in root.iter():
        if _local(inv.tag) != "invoice":
            continue
        header = _find(inv, "invoiceHeader")
        summary = _find(inv, "invoiceSummary")
        issuer = _find(inv, "issuer")
        counterpart = _find(inv, "counterpart")
        out.append({
            "mark": _text(inv, "mark"),
            "uid": _text(inv, "uid"),
            "issuer_afm": _text(issuer, "vatNumber") if issuer is not None else None,
            "counterpart_afm": _text(counterpart, "vatNumber") if counterpart is not None else None,
            "series": _text(header, "series") if header is not None else None,
            "number": _text(header, "aa") if header is not None else None,
            "date": _text(header, "issueDate") if header is not None else None,
            "type": _text(header, "invoiceType") if header is not None else None,
            "net_cents": _to_cents(_text(summary, "totalNetValue")) if summary is not None else None,
            "vat_cents": _to_cents(_text(summary, "totalVatAmount")) if summary is not None else None,
            "total_cents": _to_cents(_text(summary, "totalGrossValue")) if summary is not None else None,
        })
    return out


def request_docs(profile="store", mark="0"):
    """Invoices where this account is the counterpart (received). Returns (status, docs, raw)."""
    status, text = _call(profile, "GET", "RequestDocs", {"mark": mark})
    docs = parse_docs(text) if status == 200 and text.strip().startswith("<") else []
    return status, docs, text


def request_transmitted_docs(profile="store", mark="0"):
    """Invoices this account has sent (issued). Returns (status, docs, raw)."""
    status, text = _call(profile, "GET", "RequestTransmittedDocs", {"mark": mark})
    docs = parse_docs(text) if status == 200 and text.strip().startswith("<") else []
    return status, docs, text


# ---------- the check used by the invoices agent ----------

def _same(a, b):
    return (a or "").strip().upper() == (b or "").strip().upper()


def _lookup(profile, reader, supplier_afm, series, number):
    status, docs, _ = reader(profile)
    if status != 200:
        return None, "myDATA HTTP %d" % status
    for doc in docs:
        if (doc["issuer_afm"] == supplier_afm and _same(doc["series"], series)
                and _same(doc["number"], str(number))):
            return doc, None
    return None, None


def check_invoice(supplier_afm, series, number, total_cents):
    """Is this invoice in myDATA (dev)? Never raises.

    Plan A: the store's keys, RequestDocs (invoices received).
    Plan B: the supplier's keys, RequestTransmittedDocs (invoices sent).
    Returns {"found", "totals_match", "mark", "error"}.
    """
    plans = []
    if is_configured("store"):
        plans.append(("store", request_docs))
    if is_configured("supplier"):
        plans.append(("supplier", request_transmitted_docs))
    if not plans:
        return {"found": False, "totals_match": None, "mark": None, "error": "no myDATA keys in .env"}
    last_error = None
    for profile, reader in plans:
        try:
            doc, err = _lookup(profile, reader, supplier_afm, series, number)
        except Exception as exc:  # never raise to the caller
            doc, err = None, str(exc) if isinstance(exc, MyDataError) else "myDATA reply not readable"
        if doc:
            match = None
            if doc["total_cents"] is not None and total_cents is not None:
                match = abs(doc["total_cents"] - int(total_cents)) <= TOTAL_TOLERANCE_CENTS
            return {"found": True, "totals_match": match, "mark": doc["mark"], "error": None}
        last_error = err or last_error
    return {"found": False, "totals_match": None, "mark": None, "error": last_error}


# ---------- sending (dev only, used by the probe and the demo "supplier") ----------

def _money(cents):
    return "%d.%02d" % divmod(int(cents), 100)


def build_invoice_xml(issuer_afm, counterpart_afm, series, number, issue_date, lines,
                      invoice_type="1.1", payment_type=5):
    """Build an InvoicesDoc with one invoice.

    lines: [{"net_cents": int, "vat_rate": 24|13|6|0, optional "vat_cents": int}]
    payment_type: 5 = on credit, 1 = bank transfer, 3 = cash, 7 = card.
    """
    details = []
    net_total = vat_total = 0
    for i, line in enumerate(lines, 1):
        net = int(line["net_cents"])
        rate = int(line["vat_rate"])
        vat = int(line["vat_cents"]) if "vat_cents" in line else int(round(net * rate / 100))
        net_total += net
        vat_total += vat
        details.append(
            "<invoiceDetails>"
            f"<lineNumber>{i}</lineNumber>"
            f"<netValue>{_money(net)}</netValue>"
            f"<vatCategory>{VAT_CATEGORY[rate]}</vatCategory>"
            f"<vatAmount>{_money(vat)}</vatAmount>"
            "<incomeClassification>"
            "<icls:classificationType>E3_561_001</icls:classificationType>"
            "<icls:classificationCategory>category1_1</icls:classificationCategory>"
            f"<icls:amount>{_money(net)}</icls:amount>"
            "</incomeClassification>"
            "</invoiceDetails>"
        )
    gross = net_total + vat_total
    return (
        f'<InvoicesDoc xmlns="{NS_INV}" xmlns:icls="{NS_ICLS}" xmlns:ecls="{NS_ECLS}">'
        "<invoice>"
        f"<issuer><vatNumber>{escape(issuer_afm)}</vatNumber><country>GR</country><branch>0</branch></issuer>"
        f"<counterpart><vatNumber>{escape(counterpart_afm)}</vatNumber><country>GR</country><branch>0</branch>"
        "<address><postalCode>11635</postalCode><city>ATHINA</city></address></counterpart>"
        "<invoiceHeader>"
        f"<series>{escape(series)}</series><aa>{escape(str(number))}</aa>"
        f"<issueDate>{escape(issue_date)}</issueDate><invoiceType>{invoice_type}</invoiceType>"
        "<currency>EUR</currency>"
        "</invoiceHeader>"
        "<paymentMethods><paymentMethodDetails>"
        f"<type>{payment_type}</type><amount>{_money(gross)}</amount>"
        "</paymentMethodDetails></paymentMethods>"
        + "".join(details) +
        "<invoiceSummary>"
        f"<totalNetValue>{_money(net_total)}</totalNetValue>"
        f"<totalVatAmount>{_money(vat_total)}</totalVatAmount>"
        "<totalWithheldAmount>0.00</totalWithheldAmount>"
        "<totalFeesAmount>0.00</totalFeesAmount>"
        "<totalStampDutyAmount>0.00</totalStampDutyAmount>"
        "<totalOtherTaxesAmount>0.00</totalOtherTaxesAmount>"
        "<totalDeductionsAmount>0.00</totalDeductionsAmount>"
        f"<totalGrossValue>{_money(gross)}</totalGrossValue>"
        "<incomeClassification>"
        "<icls:classificationType>E3_561_001</icls:classificationType>"
        "<icls:classificationCategory>category1_1</icls:classificationCategory>"
        f"<icls:amount>{_money(net_total)}</icls:amount>"
        "</incomeClassification>"
        "</invoiceSummary>"
        "</invoice>"
        "</InvoicesDoc>"
    ), gross


def parse_send_response(xml_text):
    """ResponseDoc -> list of {status, mark, uid, errors[]}."""
    root = ET.fromstring(xml_text)
    out = []
    for resp in root.iter():
        if _local(resp.tag) != "response":
            continue
        errors = []
        for err in resp.iter():
            if _local(err.tag) == "error":
                errors.append({"code": _text(err, "code"), "message": _text(err, "message")})
        out.append({
            "status": _text(resp, "statusCode"),
            "mark": _text(resp, "invoiceMark"),
            "uid": _text(resp, "invoiceUid"),
            "errors": errors,
        })
    return out


def send_invoice(profile, xml_body):
    """POST SendInvoices. Returns (http_status, parsed_responses, raw)."""
    status, text = _call(profile, "POST", "SendInvoices", body=xml_body)
    parsed = parse_send_response(text) if text.strip().startswith("<") else []
    return status, parsed, text
