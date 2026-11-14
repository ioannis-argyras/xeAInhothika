"""myDATA dev probe (role C).

    python -m xangx.invoices.probe ping     one read call per configured account
    python -m xangx.invoices.probe flow     supplier sends an invoice to the store; store looks for it

Plan A: the store finds it with RequestDocs (two registrations).
Plan B: the store checks with the supplier's keys (RequestTransmittedDocs).
Keys come from .env and are never printed.
"""

import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xangx.invoices import mydata  # noqa: E402
from xangx.shared.env import load_env  # noqa: E402


def ping():
    any_ok = False
    for profile in ("store", "supplier"):
        if not mydata.is_configured(profile):
            print(f"[{profile}] not configured in .env, skipped")
            continue
        try:
            status, docs, raw = mydata.request_transmitted_docs(profile)
        except mydata.MyDataError as exc:
            print(f"[{profile}] ERROR {exc}")
            continue
        print(f"[{profile}] RequestTransmittedDocs -> HTTP {status}, {len(docs)} invoices")
        if status != 200:
            print("   reply:", raw[:300])
        any_ok = any_ok or status == 200
    return 0 if any_ok else 1


def flow():
    store_afm = os.environ.get("MYDATA_AFM", "").strip()
    supplier_afm = os.environ.get("MYDATA_SUPPLIER_AFM", "").strip()
    if not (store_afm and supplier_afm and mydata.is_configured("supplier")):
        print("Need MYDATA_AFM, MYDATA_SUPPLIER_AFM and the supplier keys in .env")
        return 1
    number = str(int(time.time()))[-6:]
    xml, gross = mydata.build_invoice_xml(
        issuer_afm=supplier_afm, counterpart_afm=store_afm,
        series="TEST", number=number, issue_date=datetime.date.today().isoformat(),
        lines=[{"net_cents": 1000, "vat_rate": 24}, {"net_cents": 500, "vat_rate": 13}],
    )
    status, responses, raw = mydata.send_invoice("supplier", xml)
    print(f"[supplier] SendInvoices TEST-{number} -> HTTP {status}")
    for r in responses:
        print("   status:", r["status"], "mark:", r["mark"], "errors:", r["errors"])
    if status != 200 or not responses or not responses[0]["mark"]:
        if not responses:
            print("   reply:", raw[:500])
        return 1
    mark = responses[0]["mark"]

    # Plan A: the store sees it as received
    if mydata.is_configured("store"):
        found = None
        for attempt in range(5):
            st, docs, raw = mydata.request_docs("store", mark=str(int(mark) - 1))
            found = next((d for d in docs if d["mark"] == mark), None)
            print(f"[store] RequestDocs try {attempt + 1} -> HTTP {st}, {len(docs)} invoices, found={bool(found)}")
            if found or st != 200:
                break
            time.sleep(3)
        if found:
            ok = found["total_cents"] == gross
            print(f"PLAN A WORKS: store sees mark {mark}, total {found['total_cents']} cents, totals_match={ok}")
            return 0
        print("Plan A: store did not see it.")
    else:
        print("Store keys missing, cannot test plan A.")

    # Plan B: check with the supplier's keys
    st, docs, _ = mydata.request_transmitted_docs("supplier", mark=str(int(mark) - 1))
    found = next((d for d in docs if d["mark"] == mark), None)
    print(f"[supplier] RequestTransmittedDocs -> HTTP {st}, found={bool(found)}")
    if found:
        print("PLAN B WORKS (supplier keys).")
        return 2
    return 1


def main():
    os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    load_env(".env")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    return {"ping": ping, "flow": flow}[cmd]()


if __name__ == "__main__":
    sys.exit(main())
