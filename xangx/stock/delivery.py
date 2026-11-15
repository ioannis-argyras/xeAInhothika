"""Delivery check with barcodes: start, scan, complete with shortages.

A product barcode counts 1 unit; the supplier's case barcode (supplier_products.case_barcode)
counts pack_size units of that product.

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


def _shown(delivery_id):
    """How the owner reads a delivery: "παραλαβή της P-0318", never the inner code "D-P-0318"."""
    m = re.fullmatch(r"D-(P-\d{1,6})", str(delivery_id))
    return f"παραλαβή της {m.group(1)}" if m else "παραλαβή"


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


def _lookup(conn, code, supplier_id):
    """(product_id, units) for a barcode: product barcode = 1, this supplier's case = pack_size."""
    row = conn.execute("SELECT id FROM products WHERE barcode = ?", (code,)).fetchone()
    if row:
        return row["id"], 1
    row = conn.execute("SELECT product_id, pack_size FROM supplier_products "
                       "WHERE case_barcode = ? AND supplier_id = ?", (code, supplier_id)).fetchone()
    if row:
        return row["product_id"], max(1, int(row["pack_size"] or 1))
    return None, 1


# The demo scene scans only 4 stickers live (Coca-Cola x2, chocolate case, juice); these were
# "scanned before" and the page adds them when opened with &skini=1 (Stavros 15/11/2026).
SCENE_READY = (("water-1500x6", "case", 9), ("chips-salt-90", "case", 2), ("choco-milk-100", "single", 6))


def scene_barcodes(conn, order_id):
    """The barcodes scanned before the demo scene, in order. Products the order's supplier has no code for are skipped."""
    order = _order(conn, order_id)
    if order is None:
        raise DeliveryError(f"Δεν βρήκα την παραγγελία {order_id}.")
    supplier = order.payload.get("supplier_id")
    codes = []
    for pid, kind, times in SCENE_READY:
        if kind == "case":
            row = conn.execute("SELECT case_barcode FROM supplier_products WHERE product_id = ? AND supplier_id = ?",
                               (pid, supplier)).fetchone()
        else:
            row = conn.execute("SELECT barcode FROM products WHERE id = ?", (pid,)).fetchone()
        if row and row[0]:
            codes += [row[0]] * times
    return codes


def scan(conn, delivery_id, barcode):
    """One scan: 1 unit, or pack_size units for a case barcode. Unknown barcodes are kept (product_id null). Returns the status."""
    started = _started(conn, delivery_id)
    if started is None:
        raise DeliveryError(f"Δεν έχει ξεκινήσει η {_shown(delivery_id)}.")
    if _completed(conn, delivery_id) is not None:
        raise DeliveryError(f"Η {_shown(delivery_id)} έχει ήδη κλείσει.")
    code = clean_barcode(barcode)
    pid, units = _lookup(conn, code, started.payload.get("supplier_id"))
    payload = {"delivery_id": delivery_id, "barcode": code, "product_id": pid}
    if units != 1:
        payload["units"] = units
    event, _ = append_event(conn, type="delivery.item_scanned", actor=SCANNER,
                            subject=f"order:{started.payload['order_id']}", caused_by=started.id,
                            payload=payload)
    result = status(conn, delivery_id)
    result["last"] = {"barcode": code, "product_id": pid, "units": units,
                      "name": _products(conn).get(pid, {}).get("name") if pid else None}
    result["event_ids"] = [event.id]
    return result


def _count(conn, delivery_id, order):
    """(lines, shortages, unknown_barcodes, extra) from the order and the scans so far."""
    scans = [(e.payload.get("product_id"), int(e.payload.get("units") or 1), e.payload["barcode"])
             for e in read_events(conn, types=["delivery.item_scanned"])
             if e.payload.get("delivery_id") == delivery_id]
    return _tally(order, scans)


def _tally(order, scans):
    """(lines, shortages, unknown_barcodes, extra) from the order and (product_id, units, barcode) scans."""
    scanned, unknown = {}, []
    for pid, units, code in scans:
        if pid:
            scanned[pid] = scanned.get(pid, 0) + units
        elif code not in unknown:
            unknown.append(code)
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
        raise DeliveryError(f"Δεν έχει ξεκινήσει η {_shown(delivery_id)}.")
    order_id = started.payload["order_id"]
    done = _completed(conn, delivery_id)
    if done is not None:
        lines = [l for l in done.payload["lines"] if l["expected"] > 0]
        extra = [l for l in done.payload["lines"] if l["expected"] == 0]
        shortages, unknown = done.payload["shortages"], done.payload["unknown_barcodes"]
    else:
        lines, shortages, unknown, extra = _count(conn, delivery_id, _order(conn, order_id))
    return _shape(conn, delivery_id, order_id, done is not None, lines, shortages, unknown, extra)


def _shape(conn, delivery_id, order_id, completed, lines, shortages, unknown, extra):
    names = {pid: p["name"] for pid, p in _products(conn).items()}
    expected = sum(l["expected"] for l in lines)
    got = sum(min(l["scanned"], l["expected"]) for l in lines)
    order = _order(conn, order_id)
    unit = {l["product_id"]: l.get("unit_cents") for l in (order.payload.get("lines", []) if order else [])}
    supplier_id = order.payload.get("supplier_id") if order else None
    row = conn.execute("SELECT name FROM suppliers WHERE id = ?", (supplier_id,)).fetchone() if supplier_id else None
    return {
        "delivery_id": delivery_id, "order_id": order_id, "completed": completed,
        "supplier_name": row["name"] if row else None,
        "scanned": got, "expected": expected,
        "lines": [dict(l, name=names.get(l["product_id"], l["product_id"])) for l in lines],
        "missing": [{"product_id": s["product_id"], "qty": s["qty"],
                     "name": names.get(s["product_id"], s["product_id"]),
                     "value_cents": s["qty"] * unit[s["product_id"]] if unit.get(s["product_id"]) else None}
                    for s in shortages],
        "extra": [dict(l, name=names.get(l["product_id"], l["product_id"])) for l in extra],
        "unknown_barcodes": unknown,
    }


def complete(conn, delivery_id):
    """delivery.completed and one stock.moved per product that came, once. Returns the status."""
    started = _started(conn, delivery_id)
    if started is None:
        raise DeliveryError(f"Δεν έχει ξεκινήσει η {_shown(delivery_id)}.")
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


MAX_PRACTICE_SCANS = 1000


def practice(conn, order_id, barcodes):
    """The same counter for a practice run: nothing is written. The phone keeps the scans."""
    order = _order(conn, order_id)
    if order is None:
        raise DeliveryError(f"Δεν βρήκα την παραγγελία {order_id}.")
    if len(barcodes) > MAX_PRACTICE_SCANS:
        raise DeliveryError("Πάρα πολλά σκαναρίσματα για δοκιμή.")
    supplier = order.payload.get("supplier_id")
    scans = []
    for b in barcodes:
        code = clean_barcode(b)
        pid, units = _lookup(conn, code, supplier)
        scans.append((pid, units, code))
    lines, shortages, unknown, extra = _tally(order, scans)
    result = _shape(conn, "practice", order_id, False, lines, shortages, unknown, extra)
    result["practice"] = True
    if scans:
        pid, units, code = scans[-1]
        result["last"] = {"barcode": code, "product_id": pid, "units": units,
                          "name": _products(conn).get(pid, {}).get("name") if pid else None}
    return result


def pending_order(conn):
    """The newest sent order whose delivery is not closed yet, or None."""
    for e in reversed(read_events(conn, types=["order.sent"])):
        order_id = e.payload.get("order_id")
        if order_id and _completed(conn, delivery_id_for(order_id)) is None and _order(conn, order_id):
            return order_id
    return None
