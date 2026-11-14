"""Fills a database with the demo shop: reference tables, 30 days of history,
the state of "this morning", and one open order for the delivery scene.

Deterministic (random.Random(1411)). All events are written by the `system`
actor with `at` in the past, one transaction per day.
"""

import json
import math
import random
from datetime import date, datetime, timedelta

from xangx.demo.catalog_data import (
    POSITIONS,
    PRODUCT_BY_ID,
    PRODUCTS,
    SUPPLIER_BY_ID,
    SUPPLIER_PRICE,
    SUPPLIER_PRODUCTS,
    SUPPLIERS,
)
from xangx.shared.events import append_events

TODAY = date(2026, 11, 14)
HISTORY_DAYS = 30
SEED = 1411
ORDER_ID = "P-0318"
ORDER_SUPPLIER = "sup-ymittou"
ORDER_LINES = [
    ("cola-330", 48),
    ("water-1500x6", 10),
    ("chips-salt-90", 24),
    ("choco-milk-100", 30),
    ("juice-orange-1l", 12),
]
# "This morning" stock, set by a count at 06:30. Cola is at 8 (threshold 6).
MORNING_COUNTS = {"cola-330": 8, "chips-salt-90": 5, "juice-orange-1l": 3}

# Receipts per hour of the day (local time): quiet at night, busy afternoon and evening.
HOUR_WEIGHTS = [
    1.0, 0.8, 0.6, 0.5, 0.5, 0.8, 1.5, 2.5, 3.0, 3.0, 3.5, 4.0,
    5.0, 5.0, 4.5, 4.5, 5.0, 6.5, 8.0, 9.0, 9.0, 8.0, 6.0, 3.0,
]
UNITS_PER_DAY = 300  # expected units sold per day, for the order-up-to levels


def _utc(day, hour, minute, second=0, ms=0):
    """Local Athens time to the schema's UTC string. Summer time ended on 25/10/2026."""
    offset = 3 if day < date(2026, 10, 25) else 2
    local = datetime(day.year, day.month, day.day, hour, minute, second)
    return (local - timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def _event(type, at, payload, subject=None, idem_key=None):
    return dict(
        type=type, actor="system", at=at, payload=payload,
        subject=subject, idem_key=idem_key,
    )


def seed_reference(conn):
    """Products, suppliers, supplier_products and positions."""
    with conn:
        for p in PRODUCTS:
            conn.execute(
                "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?)",
                (p["id"], p["name"], p["barcode"], p["category"], p["vat_rate"],
                 p["sell_price_cents"], p["low_threshold"]),
            )
        for s in SUPPLIERS:
            conn.execute(
                "INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?,?,?)",
                (s["id"], s["name"], s["afm"], s["email"], s["phone"],
                 json.dumps([s["iban"]]), s["credit_days"]),
            )
        for sp in SUPPLIER_PRODUCTS:
            conn.execute(
                "INSERT OR REPLACE INTO supplier_products VALUES (?,?,?,?)",
                (sp["supplier_id"], sp["product_id"], sp["supplier_code"],
                 sp["pack_size"]),
            )
        for pos in POSITIONS:
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?)",
                (pos["id"], pos["name"], pos["product_id"], pos["camera_id"]),
            )


def _stock_move(at, product_id, qty, reason, unit_cost=None):
    payload = {"product_id": product_id, "qty": qty, "reason": reason}
    if unit_cost is not None:
        payload["unit_cost_cents"] = unit_cost
    return _event("stock.moved", at, payload, subject=f"product:{product_id}")


def _invoice(day, supplier, number, lines):
    net = sum(line["net_cents"] for line in lines)
    vat_by_rate = {}
    for line in lines:
        key = str(line["vat_rate"])
        vat_by_rate[key] = vat_by_rate.get(key, 0) + round(
            line["net_cents"] * line["vat_rate"] / 100
        )
    total = net + sum(vat_by_rate.values())
    invoice_id = f"INV-{supplier['id'][4:8].upper()}-{number:04d}"
    payload = {
        "invoice_id": invoice_id,
        "supplier_afm": supplier["afm"],
        "series": supplier["series"],
        "number": str(number),
        "date": day.isoformat(),
        "lines": lines,
        "net_cents": net,
        "vat_by_rate": vat_by_rate,
        "total_cents": total,
        "iban": supplier["iban"],
        "checks": {"arithmetic": True, "afm_valid": True, "pages_complete": True},
        "confidence": "high",
    }
    key = f"invoice:{supplier['afm']}:{supplier['series']}-{number}"
    return _event(
        "invoice.read", _utc(day, 8, 45), payload,
        subject=f"invoice:{invoice_id}", idem_key=key,
    )


def seed_history(conn):
    """30 days of sales, deliveries and invoices, then the morning state and the open order."""
    rng = random.Random(SEED)
    total_weight = sum(p["weight"] for p in PRODUCTS)
    rate = {p["id"]: p["weight"] / total_weight * UNITS_PER_DAY for p in PRODUCTS}
    ids = [p["id"] for p in PRODUCTS]
    weights = [p["weight"] for p in PRODUCTS]
    on_hand = {}
    invoice_no = {s["id"]: 100 for s in SUPPLIERS}
    receipt_no = 0
    start = TODAY - timedelta(days=HISTORY_DAYS)

    for day_index in range(HISTORY_DAYS):
        day = start + timedelta(days=day_index)
        events = []

        if day_index == 0:
            for p in PRODUCTS:
                target = math.ceil(rate[p["id"]] * 8) + p["low_threshold"]
                on_hand[p["id"]] = target
                events.append(_stock_move(_utc(day, 0, 0), p["id"], target, "count"))

        # Today's timeline: sales by minute of the day, plus the delivery at 08:30.
        receipts = rng.randint(120, 180)
        minutes = []
        for _ in range(receipts):
            hour = rng.choices(range(24), HOUR_WEIGHTS)[0]
            minutes.append((hour, rng.randint(0, 59), rng.randint(0, 59), rng.randint(0, 999)))
        minutes.sort()
        timeline = [(8, 30, 0, 0, "delivery")] + [m + ("sale",) for m in minutes]
        timeline.sort(key=lambda item: item[:4])

        for hour, minute, second, ms, kind in timeline:
            if kind == "delivery":
                for supplier in SUPPLIERS:
                    if (day_index - supplier["offset"]) % supplier["interval_days"] != 0:
                        continue
                    lines = []
                    for p in PRODUCTS:
                        if p["main_supplier"] != supplier["id"]:
                            continue
                        target = math.ceil(
                            rate[p["id"]] * (supplier["interval_days"] + 2)
                        ) + p["low_threshold"]
                        if on_hand[p["id"]] > target * 0.55:
                            continue
                        pack = p["pack_size"]
                        qty = math.ceil((target - on_hand[p["id"]]) / pack) * pack
                        unit = SUPPLIER_PRICE[(supplier["id"], p["id"])]
                        lines.append(
                            {"name": p["name"], "product_id": p["id"], "qty": qty,
                             "unit_cents": unit, "net_cents": qty * unit,
                             "vat_rate": p["vat_rate"]}
                        )
                        on_hand[p["id"]] += qty
                        events.append(
                            _stock_move(_utc(day, 8, 30), p["id"], qty, "delivery", unit)
                        )
                    if lines:
                        invoice_no[supplier["id"]] += 1
                        events.append(
                            _invoice(day, supplier, invoice_no[supplier["id"]], lines)
                        )
                continue

            # A sale
            receipt_no += 1
            receipt_id = f"R-{receipt_no:06d}"
            at = _utc(day, hour, minute, second, ms)
            wanted = rng.choices([1, 2, 3], [55, 30, 15])[0]
            chosen = {}
            for _ in range(wanted * 3):
                if len(chosen) >= wanted:
                    break
                pid = rng.choices(ids, weights)[0]
                if pid in chosen or on_hand[pid] < 1:
                    continue
                qty = min(rng.choices([1, 2, 3], [78, 17, 5])[0], on_hand[pid])
                chosen[pid] = qty
            if not chosen:
                receipt_no -= 1
                continue
            lines = []
            moves = []
            for pid, qty in chosen.items():
                product = PRODUCT_BY_ID[pid]
                gross = qty * product["sell_price_cents"]
                lines.append({"product_id": pid, "qty": qty, "gross_cents": gross})
                on_hand[pid] -= qty
                moves.append(_stock_move(at, pid, -qty, "sale"))
            payload = {
                "receipt_id": receipt_id,
                "lines": lines,
                "total_cents": sum(line["gross_cents"] for line in lines),
                "payment": rng.choices(["card", "cash"], [62, 38])[0],
            }
            events.append(
                _event("sale.recorded", at, payload, idem_key=f"sale:{receipt_id}")
            )
            events.extend(moves)

        append_events(conn, events)

    _seed_morning(conn, on_hand, rate)


def _seed_morning(conn, on_hand, rate):
    """The state of this morning: a count at 06:30, then the open order P-0318."""
    events = []
    at = _utc(TODAY, 6, 30)
    for p in PRODUCTS:
        pid = p["id"]
        current = on_hand[pid]
        if pid in MORNING_COUNTS:
            wanted = MORNING_COUNTS[pid]
        elif current <= p["low_threshold"]:
            wanted = p["low_threshold"] * 2
        else:
            continue
        if wanted != current:
            events.append(_stock_move(at, pid, wanted - current, "count"))
        on_hand[pid] = wanted

    supplier = SUPPLIER_BY_ID[ORDER_SUPPLIER]
    lines = []
    for pid, qty in ORDER_LINES:
        unit = SUPPLIER_PRICE[(ORDER_SUPPLIER, pid)]
        lines.append(
            {"product_id": pid, "qty": qty, "unit_cents": unit, "last_unit_cents": unit}
        )
    net = sum(line["qty"] * line["unit_cents"] for line in lines)
    events.append(
        _event(
            "order.proposed", _utc(TODAY, 6, 45),
            {
                "order_id": ORDER_ID, "supplier_id": ORDER_SUPPLIER, "lines": lines,
                "net_cents": net,
                "why": {"per_day": round(rate["cola-330"], 1), "lead_days": 1,
                        "on_hand": on_hand["cola-330"]},
            },
            subject=f"order:{ORDER_ID}",
        )
    )
    events.append(
        _event(
            "order.sent", _utc(TODAY, 6, 50),
            {"order_id": ORDER_ID, "to": supplier["email"],
             "message_id": f"<{ORDER_ID.lower()}@example.com>"},
            subject=f"order:{ORDER_ID}", idem_key=f"order.sent:{ORDER_ID}",
        )
    )
    append_events(conn, events)


def seed_all(conn):
    """Reference tables plus history. The database must already have its tables."""
    seed_reference(conn)
    seed_history(conn)
