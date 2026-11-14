"""Send the 3 demo invoices to myDATA dev as the "supplier" (role C, demo only).

    python -m xangx.invoices.send_demo           dry run: shows what would be sent
    python -m xangx.invoices.send_demo --send    sends the ones not already in myDATA

myDATA only accepts invoices issued under the sender's own AFM, so all 3 go out with the
supplier account's AFM (MYDATA_SUPPLIER_AFM). The seed AFMs are mapped to it with
MYDATA_AFM_MAP in .env, so the invoice check finds them. The issue date and the total
come from the printed demo invoice (data/demo_invoices/<file>.html), so myDATA matches the paper.
Never sends twice: anything already sent (same series and number) is skipped.
Keys come from .env and are never printed.
"""

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.make_demo_invoices import OUT, _eur, invoices, totals  # noqa: E402
from xangx.invoices import mydata  # noqa: E402
from xangx.invoices.core import _mydata_afm  # noqa: E402
from xangx.shared.env import load_env  # noqa: E402

DEMO_FILES = ("A-2271-ymittou", "B-512-artopoieio", "G-318-galakto")  # not the TEST trap


def demo_invoices():
    return [inv for inv in invoices() if inv["file"] in DEMO_FILES]


def printed_date(html_text):
    """The issue date printed on the demo invoice (DD/MM/YYYY) as YYYY-MM-DD, or None."""
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", html_text)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def lines_with_vat(lines):
    """Per-line VAT that adds up exactly to the invoice's VAT per rate (as printed)."""
    _, by_rate, vat_by_rate, _ = totals(lines)
    out = [{"net_cents": ln["net_cents"], "vat_rate": ln["vat_rate"],
            "vat_cents": int(round(ln["net_cents"] * ln["vat_rate"] / 100))} for ln in lines]
    for rate, vat in vat_by_rate.items():
        same = [ln for ln in out if ln["vat_rate"] == rate]
        biggest = max(same, key=lambda ln: ln["net_cents"])
        biggest["vat_cents"] += vat - sum(ln["vat_cents"] for ln in same)
    return out


def plan(inv, supplier_afm, store_afm, html_text):
    """Everything needed to send one invoice, or {"error": ...}. No network."""
    label = f"{inv['series']}-{inv['number']}"
    _, _, _, gross = totals(inv["lines"])
    issue_date = printed_date(html_text)
    if not issue_date:
        return {"label": label, "error": "no date on the printed invoice"}
    if _eur(gross) not in html_text:
        return {"label": label, "error": f"total {_eur(gross)} not on the printed invoice"}
    mapped = _mydata_afm(inv["supplier"]["afm"])
    if mapped != supplier_afm:
        return {"label": label,
                "error": f"MYDATA_AFM_MAP needs {inv['supplier']['afm']}={supplier_afm}"}
    xml, xml_gross = mydata.build_invoice_xml(
        issuer_afm=supplier_afm, counterpart_afm=store_afm, series=inv["series"],
        number=inv["number"], issue_date=issue_date, lines=lines_with_vat(inv["lines"]))
    if xml_gross != gross:
        return {"label": label, "error": f"myDATA total {xml_gross} != printed {gross} cents"}
    return {"label": label, "series": inv["series"], "number": inv["number"],
            "date": issue_date, "gross": gross, "xml": xml}


def already_sent(docs, supplier_afm, series, number):
    return next((d for d in docs if d["issuer_afm"] == supplier_afm
                 and mydata._same(d["series"], series) and mydata._same(d["number"], number)), None)


def run(send=False):
    store_afm = os.environ.get("MYDATA_AFM", "").strip()
    supplier_afm = os.environ.get("MYDATA_SUPPLIER_AFM", "").strip()
    if not (store_afm and supplier_afm and mydata.is_configured("supplier")):
        print("Need MYDATA_AFM, MYDATA_SUPPLIER_AFM and the supplier keys in .env")
        return 1

    plans = []
    for inv in demo_invoices():
        page = Path(OUT) / f"{inv['file']}.html"
        if not page.exists():
            print(f"{inv['file']}: missing {page}, run scripts/make_demo_invoices.py --pdf first")
            return 1
        plans.append(plan(inv, supplier_afm, store_afm, page.read_text(encoding="utf-8")))
    bad = [p for p in plans if "error" in p]
    for p in bad:
        print(f"{p['label']}: STOP, {p['error']}")
    if bad:
        return 1

    status, sent_docs, _ = mydata.request_transmitted_docs("supplier")
    if status != 200:
        print(f"[supplier] RequestTransmittedDocs -> HTTP {status}, stopping (cannot rule out a double send)")
        return 1

    failed = 0
    for p in plans:
        line = f"{p['label']}  {p['date']}  {_eur(p['gross'])} €"
        done = already_sent(sent_docs, supplier_afm, p["series"], p["number"])
        if done:
            print(f"{line}  already in myDATA (mark {done['mark']}), skipped")
            continue
        if not send:
            print(f"{line}  would be sent (dry run, add --send)")
            continue
        st, responses, raw = mydata.send_invoice("supplier", p["xml"])
        r = responses[0] if responses else {}
        if st == 200 and r.get("mark"):
            print(f"{line}  SENT, mark {r['mark']}")
        else:
            failed += 1
            print(f"{line}  FAILED, HTTP {st}, status {r.get('status')}, errors {r.get('errors')}")
            if not responses:
                print("   reply:", raw[:300])
    if send and not failed:
        return verify(plans)
    return 1 if failed else 0


def verify(plans):
    """The store's side: does the invoice check find each one with the same total?"""
    failed = 0
    for p in plans:
        r = {}
        for attempt in range(5):
            r = mydata.check_invoice(os.environ["MYDATA_SUPPLIER_AFM"].strip(),
                                     p["series"], p["number"], p["gross"])
            if r["found"] or r["error"]:
                break
            time.sleep(3)
        ok = r["found"] and r["totals_match"]
        failed += not ok
        print(f"check {p['label']}: found={r['found']} totals_match={r['totals_match']} "
              f"mark={r['mark']} error={r['error']}")
    return 1 if failed else 0


def main():
    os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    load_env(".env")
    return run(send="--send" in sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
