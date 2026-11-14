"""Stock tools for the partner (master): how a delivery is going."""

from xangx.master.tools import tool
from xangx.stock import delivery, expiry


@tool(
    name="delivery_status",
    description=(
        "How the barcode check of a delivery is going, for one order: how many units came "
        "out of how many ordered, what is missing, unknown barcodes, and whether it is closed."
    ),
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "e.g. P-0318"}},
        "required": ["order_id"],
    },
)
def delivery_status(conn, order_id):
    try:
        s = delivery.status(conn, delivery.delivery_id_for(order_id))
    except delivery.DeliveryError:
        return {"summary": f"Δεν έχει ξεκινήσει παραλαβή για την {order_id}.", "event_ids": []}
    missing = ", ".join(f"{m['qty']} {m['name']}" for m in s["missing"]) or "τίποτα"
    state = "Έκλεισε" if s["completed"] else "Σε εξέλιξη"
    s["summary"] = (f"{state} η παραλαβή της {order_id}: {s['scanned']} από {s['expected']}, "
                    f"λείπουν: {missing}.")
    s["event_ids"] = []
    return s


@tool(
    name="expiring_soon",
    description=(
        "Which batches in the store expire soon (or already expired): product, quantity and "
        "date. Batches are counted from the day the goods came in, oldest sold first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "within_days": {"type": "integer", "description": "Look this many days ahead, e.g. 3"},
        },
        "required": ["within_days"],
    },
)
def expiring_soon(conn, within_days):
    items = expiry.expiring(conn, within_days=max(int(within_days), 0))
    return {"items": items, "summary": expiry.summary_text(items, within_days), "event_ids": []}
