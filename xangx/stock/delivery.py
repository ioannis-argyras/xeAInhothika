"""Delivery check with barcodes: start, scan (1 unit per scan), complete with shortages.

Events: delivery.started and delivery.completed (actor `stock`), delivery.item_scanned
(actor `scanner`), and one stock.moved reason "delivery" per product on completion.
"""

import re

from xangx.shared import views
from xangx.shared.events import append_event, append_events, read_events

ACTOR = "stock"
SCANNER = "scanner"


class DeliveryError(ValueError):
    """A request the delivery cannot take (unknown order, already completed, bad barcode)."""


def delivery_id_for(order_id):
    return f"D-{order_id}"


def _order(conn, order_id):
    return next((e for e in read_events(conn, types=["order.proposed"])
                 if e.payload.get("order_id") == order_id), None)


def _started(conn, delivery_id):
    return next((e for e in read_events(conn, types=["delivery.started"])
                 if e.payload.get("delivery_id") == delivery_id), None)


def _completed(conn, delivery_id):
    return next((e for e in read_events(conn, types=["delivery.completed"])
                 if e.payload.get("delivery_id") == delivery_id), None)


def _products(conn):
    return {r["id"]: dict(r) for r in conn.execute("SELECT id, name, barcode FROM products")}


def start(conn, order_id):
    """delivery.started for this order (once: idem_key D-<order_id>). Returns the status."""
    order = _order(conn, order_id)
    if order is None:
        raise DeliveryError(f"Δεν βρήκα την παραγγελία {order_id}.")
    delivery_id = delivery_id_for(order_id)
    append_event(conn, type="delivery.started", actor=ACTOR, subject=f"order:{order_id}",
                 idem_key=delivery_id, caused_by=order.id,
                 payload={"delivery_id": delivery_id, "order_id": order_id,
                          "supplier_id": order.payload["supplier_id"]})
    return status(conn, delivery_id)


def clean_barcode(barcode):
    code = re.sub(r"\s+", "", str(barcode or ""))
    if not re.fullmatch(r"\d{6,14}", code):
        raise DeliveryError("Το barcode πρέπει να έχει 6 έως 14 ψηφία.")
    return code


def scan(conn, delivery_id, barcode):
    """One scan = one unit. Unknown barcodes are kept (product_id null). Returns the status."""
    started = _started(conn, delivery_id)
    if started is None:
        raise DeliveryError(f"Δεν έχει ξεκινήσει η παραλαβή {delivery_id}.")
    if _completed(conn, delivery_id) is not None:
        raise DeliveryError(f"Η παραλαβή {delivery_id} έχει ήδη κλείσει.")
    code = clean_barcode(barcode)
    row = conn.execute("SELECT id FROM products WHERE barcode = ?", (code,)).fetchone()
    pid = row["id"] if row else None
    event, _ = append_event(conn, type="delivery.item_scanned", actor=SCANNER,
                            subject=f"order:{started.payload['order_id']}", caused_by=started.id,
                            payload={"delivery_id": delivery_id, "barcode": code, "product_id": pid})
    result = status(conn, delivery_id)
    result["last"] = {"barcode": code, "product_id": pid,
                      "name": _products(conn).get(pid, {}).get("name") if pid else None}
    result["event_ids"] = [event.id]
    return result


def _count(conn, delivery_id, order):
    """(lines, shortages, unknown_barcodes, extra) from the order and the scans so far."""
    scanned, unknown = {}, []
    for e in read_events(conn, types=["delivery.item_scanned"]):
        if e.payload.get("delivery_id") != delivery_id:
            continue
        pid = e.payload.get("product_id")
        if pid:
            scanned[pid] = scanned.get(pid, 0) + 1
        elif e.payload["barcode"] not in unknown:
            unknown.append(e.payload["barcode"])
    expected = {}
    for l in order.payload.get("lines", []):
        expected[l["product_id"]] = expected.get(l["product_id"], 0) + l["qty"]
    lines = [{"product_id": pid, "expected": qty, "scanned": scanned.get(pid, 0)}
             for pid, qty in expected.items()]
    extra = [{"product_id": pid, "expected": 0, "scanned": n}
             for pid, n in scanned.items() if pid not in expected]
    shortages = [{"product_id": l["product_id"], "qty": l["expected"] - l["scanned"]}
                 for l in lines if l["scanned"] < l["expected"]]
    return lines, shortages, unknown, extra


def status(conn, delivery_id):
    """The counter for the phone page and the partner: how many came, what is missing."""
    started = _started(conn, delivery_id)
    if started is None:
        raise DeliveryError(f"Δεν έχει ξεκινήσει η παραλαβή {delivery_id}.")
    order_id = started.payload["order_id"]
    done = _completed(conn, delivery_id)
    if done is not None:
        lines = [l for l in done.payload["lines"] if l["expected"] > 0]
        extra = [l for l in done.payload["lines"] if l["expected"] == 0]
        shortages, unknown = done.payload["shortages"], done.payload["unknown_barcodes"]
    else:
        lines, shortages, unknown, extra = _count(conn, delivery_id, _order(conn, order_id))
    names = {pid: p["name"] for pid, p in _products(conn).items()}
    expected = sum(l["expected"] for l in lines)
    got = sum(min(l["scanned"], l["expected"]) for l in lines)
    return {
        "delivery_id": delivery_id, "order_id": order_id, "completed": done is not None,
        "scanned": got, "expected": expected,
        "lines": [dict(l, name=names.get(l["product_id"], l["product_id"])) for l in lines],
        "missing": [{"product_id": s["product_id"], "qty": s["qty"],
                     "name": names.get(s["product_id"], s["product_id"])} for s in shortages],
        "extra": [dict(l, name=names.get(l["product_id"], l["product_id"])) for l in extra],
        "unknown_barcodes": unknown,
    }


def complete(conn, delivery_id):
    """delivery.completed and one stock.moved per product that came, once. Returns the status."""
    started = _started(conn, delivery_id)
    if started is None:
        raise DeliveryError(f"Δεν έχει ξεκινήσει η παραλαβή {delivery_id}.")
    if _completed(conn, delivery_id) is not None:
        return status(conn, delivery_id)
    order = _order(conn, started.payload["order_id"])
    lines, shortages, unknown, extra = _count(conn, delivery_id, order)
    unit = {l["product_id"]: l.get("unit_cents") for l in order.payload.get("lines", [])}
    items = [{"type": "delivery.completed", "actor": ACTOR, "caused_by": started.id,
              "subject": f"order:{order.payload['order_id']}",
              "idem_key": f"delivery.completed:{delivery_id}",
              "payload": {"delivery_id": delivery_id, "lines": lines + extra,
                          "shortages": shortages, "unknown_barcodes": unknown}}]
    for l in lines + extra:
        if l["scanned"] <= 0:
            continue
        payload = {"product_id": l["product_id"], "qty": l["scanned"], "reason": "delivery"}
        cost = unit.get(l["product_id"]) or views.last_unit_price_cents(conn, l["product_id"])
        if cost is not None:
            payload["unit_cost_cents"] = cost
        items.append({"type": "stock.moved", "actor": ACTOR, "payload": payload,
                      "subject": f"product:{l['product_id']}", "caused_by": started.id,
                      "idem_key": f"delivery:{delivery_id}:{l['product_id']}"})
    append_events(conn, items)
    return status(conn, delivery_id)
