"""Invoice tools for the partner (master). None needs approval."""

from xangx.invoices import core
from xangx.master.tools import tool


@tool(
    name="list_open_invoices",
    description=(
        "List supplier invoices that arrived but are not processed yet, or that have "
        "issues (shortage, price change, failed checks, new IBAN)."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
)
def list_open_invoices(conn):
    items = core.open_invoices(conn)
    if not items:
        return {"summary": "Δεν υπάρχουν εκκρεμή τιμολόγια.", "event_ids": [], "invoices": []}
    lines = [core.summary(conn, i["invoice_id"]) for i in items]
    return {
        "summary": f"{len(items)} τιμολόγια θέλουν προσοχή.",
        "event_ids": [],
        "invoices": [dict(i, text=t) for i, t in zip(items, lines)],
    }


@tool(
    name="process_invoice",
    description=(
        "Process one received invoice: read it, run the arithmetic/AFM/page checks, "
        "match it with the order and the delivery, record price changes and new IBANs, "
        "and check it in myDATA (test environment). Safe to call twice: done once."
    ),
    input_schema={
        "type": "object",
        "properties": {"invoice_id": {"type": "string", "description": "e.g. INV-1a2b3c4d"}},
        "required": ["invoice_id"],
    },
)
def process_invoice(conn, invoice_id):
    result = core.process_invoice(conn, invoice_id)
    return {"summary": result["summary"], "event_ids": result["event_ids"]}
