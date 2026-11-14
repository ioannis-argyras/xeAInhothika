"""Accountant tools for the partner. Only request_credit needs the owner's yes."""

from datetime import date

from xangx.accountant import core
from xangx.master.tools import tool
from xangx.shared import connect

_INVOICE = {
    "type": "object",
    "properties": {"invoice_id": {"type": "string", "description": "e.g. INV-1a2b3c4d"}},
    "required": ["invoice_id"],
}


@tool(
    name="set_payment_reminder",
    description=(
        "Set a payment reminder for a read invoice: due date = invoice date + the "
        "supplier's credit days. It never pays anything."
    ),
    input_schema=_INVOICE,
)
def set_payment_reminder(conn, invoice_id):
    return core.set_payment_reminder(conn, invoice_id)


def _describe_credit(args):
    conn = connect()
    try:
        return core.describe_credit(conn, args.get("invoice_id", ""))
    finally:
        conn.close()


@tool(
    name="request_credit",
    description=(
        "Ask the supplier for a credit note for items invoiced but not delivered "
        "(shortages found when matching the invoice). Sends an email, so it needs the owner's yes."
    ),
    input_schema=_INVOICE,
    needs_approval=True,
    approval_action="request_credit",
    describe=_describe_credit,
)
def request_credit(conn, invoice_id):
    return core.request_credit(conn, invoice_id)


@tool(
    name="daily_report",
    description=(
        "Daily report for one day (Greek time): sales, purchases, gross profit and the "
        "exceptions flagged that day. Tax figures are support information, not advice."
    ),
    input_schema={
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
        "required": ["date"],
    },
)
def daily_report(conn, date_str=None, **kwargs):
    value = kwargs.get("date") or date_str
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        return {"summary": "Η ημερομηνία θέλει μορφή ΕΕΕΕ-ΜΜ-ΗΗ.", "event_ids": []}
    return core.daily_report(conn, day)
