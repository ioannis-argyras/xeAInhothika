"""Answers computed from the events. Every view accepts until_id (time machine)."""

from xangx.shared.events import read_events


def stock_on_hand(conn, product_id, until_id=None):
    """Sum of qty over the stock.moved events of the product."""
    total = 0
    for event in read_events(conn, until_id=until_id, types=["stock.moved"]):
        if event.payload.get("product_id") == product_id:
            total += event.payload.get("qty", 0)
    return total


def pending_approvals(conn, until_id=None):
    """approval.requested events that have no approval.decided yet."""
    events = read_events(
        conn, until_id=until_id, types=["approval.requested", "approval.decided"]
    )
    decided = {
        e.payload.get("approval_id") for e in events if e.type == "approval.decided"
    }
    return [
        e
        for e in events
        if e.type == "approval.requested"
        and e.payload.get("approval_id") not in decided
    ]


def order_status(conn, order_id, until_id=None):
    """Type of the latest order.* event for the order, or None."""
    status = None
    for event in read_events(conn, until_id=until_id):
        if event.type.startswith("order.") and event.payload.get("order_id") == order_id:
            status = event.type
    return status


def last_unit_price_cents(conn, product_id, supplier_afm=None, until_id=None):
    """Unit price from the most recent invoice.read line with this product."""
    events = read_events(conn, until_id=until_id, types=["invoice.read"])
    for event in reversed(events):
        if supplier_afm is not None and event.payload.get("supplier_afm") != supplier_afm:
            continue
        for line in reversed(event.payload.get("lines", [])):
            if line.get("product_id") == product_id:
                return line.get("unit_cents")
    return None


def is_paused(conn, until_id=None):
    """True if the latest of system.paused / system.resumed is paused."""
    events = read_events(
        conn, until_id=until_id, types=["system.paused", "system.resumed"]
    )
    return bool(events) and events[-1].type == "system.paused"


def stock_levels(conn, until_id=None):
    """Stock on hand for every product, in ONE pass: {product_id: qty}."""
    sql = (
        "SELECT json_extract(payload, '$.product_id') AS pid, "
        "SUM(json_extract(payload, '$.qty')) AS total "
        "FROM events WHERE type = 'stock.moved'"
    )
    params = []
    if until_id is not None:
        sql += " AND id <= ?"
        params.append(until_id)
    sql += " GROUP BY pid"
    return {
        row["pid"]: row["total"] for row in conn.execute(sql, params) if row["pid"]
    }
