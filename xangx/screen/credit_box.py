"""What a pending credit request says, shared by the home screen and the invoices pages.

Read only. Amounts are whole cents. The supplier's email is never part of the result.
"""

from xangx.accountant.core import credit_details
from xangx.screen.api_b1 import _label
from xangx.screen.describe import load_names
from xangx.shared import views


def credit_boxes(conn, until_id, bundles):
    """approval_id -> what the credit request says, for each pending request_credit."""
    names_product = load_names(conn)["product"]
    out = {}
    for e in views.pending_approvals(conn, until_id=until_id):
        p = e.payload
        if p.get("action") != "request_credit":
            continue
        ref = str(p.get("ref") or "")
        invoice_id = (p.get("args") or {}).get("invoice_id") or (
            ref[len("invoice:"):] if ref.startswith("invoice:") else None)
        d = credit_details(conn, invoice_id) if invoice_id else None
        matched = (bundles.get(invoice_id) or {}).get("matched")
        if d is None or matched is None:
            continue
        shortages = [i for i in matched.payload.get("issues", []) if i.get("kind") == "shortage"]
        if not shortages:
            continue
        s = shortages[0]
        missing = s["expected"] - s["actual"]
        row = conn.execute("SELECT vat_rate FROM products WHERE id = ?", (s["product_id"],)).fetchone()
        out[p.get("approval_id")] = {
            "invoice_id": invoice_id,
            "label": _label(bundles.get(invoice_id, {}), invoice_id),
            "supplier": d["supplier"]["name"],
            "subject": d["subject"],
            "amount_cents": d["amount_cents"],
            "product": names_product.get(s["product_id"]) or str(s["product_id"]),
            "more": len(shortages) - 1,
            "order_id": matched.payload.get("order_id"),
            "expected": s["expected"], "actual": s["actual"], "missing": missing,
            "unit_cents": round(s["amount_cents"] / missing) if missing else 0,
            "vat_rate": int(row["vat_rate"]) if row else 0,
        }
    return out
