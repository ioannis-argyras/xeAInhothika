"""Catalog of event types: who may write each one and which payload fields are required.

Only top-level required fields are listed (fields without `?` in the schema).
Fields inside lists are not checked.
"""

from dataclasses import dataclass

ACTORS = frozenset(
    {
        "owner",
        "master",
        "stock",
        "orders",
        "invoices",
        "accountant",
        "camera",
        "scanner",
        "pos-sim",
        "system",
    }
)


@dataclass(frozen=True)
class EventSpec:
    actors: frozenset
    required: tuple


def _spec(actors, *required):
    return EventSpec(actors=frozenset(actors), required=tuple(required))


CATALOG = {
    # Warehouse
    "shelf.items_taken": _spec(
        ["camera"],
        "position_id", "product_id", "taken", "confidence", "file_ref", "prev_file_ref",
    ),
    "shelf.items_added": _spec(
        ["camera"], "position_id", "product_id", "added", "confidence", "file_ref"
    ),
    "stock.moved": _spec(
        ["stock", "pos-sim", "invoices"], "product_id", "qty", "reason"
    ),
    "stock.low_detected": _spec(
        ["stock"], "product_id", "on_hand", "per_day", "days_left", "source"
    ),
    "delivery.started": _spec(["stock"], "delivery_id", "order_id", "supplier_id"),
    "delivery.item_scanned": _spec(["scanner"], "delivery_id", "barcode"),
    "delivery.completed": _spec(
        ["stock"], "delivery_id", "lines", "shortages", "unknown_barcodes"
    ),
    # Orders
    "order.proposed": _spec(
        ["orders"], "order_id", "supplier_id", "lines", "net_cents", "why"
    ),
    "order.sent": _spec(["orders"], "order_id", "to", "message_id"),
    "order.reply_received": _spec(["orders"], "order_id", "summary", "file_ref"),
    "order.cancelled": _spec(["orders", "owner"], "order_id", "reason"),
    # Invoices
    "invoice.received": _spec(
        ["invoices"], "invoice_id", "source", "file_ref", "sha256"
    ),
    "invoice.read": _spec(
        ["invoices", "system"],
        "invoice_id", "supplier_afm", "series", "number", "date", "lines",
        "net_cents", "vat_by_rate", "total_cents", "checks", "confidence",
    ),
    "invoice.duplicate_suspected": _spec(
        ["invoices"], "invoice_id", "other_invoice_id", "rule"
    ),
    "invoice.matched": _spec(["invoices"], "invoice_id", "issues"),
    "invoice.mydata_checked": _spec(
        ["invoices"], "invoice_id", "env", "found", "totals_match"
    ),
    "price.changed": _spec(
        ["invoices"], "product_id", "supplier_id", "old_cents", "new_cents", "pct"
    ),
    "supplier.new_iban_seen": _spec(
        ["invoices"], "supplier_id", "invoice_id", "iban_last4"
    ),
    "credit.requested": _spec(
        ["invoices"], "invoice_id", "amount_cents", "reason", "message_id"
    ),
    # Accountant
    "report.daily_created": _spec(
        ["accountant"],
        "date", "sales_cents", "purchases_cents", "gross_profit_cents", "exceptions",
    ),
    "report.month_created": _spec(
        ["accountant"], "month", "invoice_count", "total_cents", "file_ref"
    ),
    "exception.flagged": _spec(["accountant"], "kind", "ref", "note"),
    "payment.reminder_set": _spec(
        ["accountant"], "supplier_id", "invoice_id", "due_on", "amount_cents"
    ),
    "payment.asked": _spec(
        ["accountant"], "invoice_id", "supplier_id", "amount_cents", "invoice_date"
    ),
    "payment.recorded": _spec(
        ["owner", "accountant"], "invoice_id", "method", "amount_cents"
    ),
    # Point of sale (simulation)
    "sale.recorded": _spec(
        ["pos-sim"], "receipt_id", "lines", "total_cents", "payment"
    ),
    # Master, owner and system
    "owner.said": _spec(["owner"], "text", "channel"),
    "agent.woke": _spec(["master"], "reason", "trigger_ids"),
    "agent.planned": _spec(["master"], "steps"),
    "agent.tool_ran": _spec(
        ["master"], "tool", "summary", "ok", "ms", "result_event_ids"
    ),
    "agent.said": _spec(["master"], "text", "channel"),
    "approval.requested": _spec(
        ["master"], "approval_id", "action", "summary", "exact", "why", "ref"
    ),
    "approval.decided": _spec(["owner"], "approval_id", "decision", "channel"),
    "notification.sent": _spec(["master"], "channel", "text"),
    "system.paused": _spec(["owner"]),
    "system.resumed": _spec(["owner"]),
    "action.undone": _spec(["owner", "master"], "event_id", "how"),
    "check.morning_done": _spec(["master"], "findings"),
}
