"""Stock tools for the partner (master): how a delivery is going."""

from xangx.master.tools import tool
from xangx.stock import delivery


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
