"""Fills a database with the demo shop: reference tables, 30 days of history,
the state of "this morning", and one open order for the delivery scene.

Deterministic (random.Random(1411)). All events are written by the `system`
actor with `at` in the past, one transaction per day.
"""

import json
import math
import random
from datetime import date, datetime, timedelta, timezone

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
# So that the Shelf page has something to show under "expires soon": the batch of milk that came
# 7 days ago counts as entered a day later (expires tomorrow), the batch of yogurt that came
# 7 days ago as entered 12 days earlier (expires in 2 days). Only the date of the stock move
# changes, not the stock. Key: (product, days before today of the delivery) -> days to shift.
EXPIRY_SHIFT = {("milk-fresh-1l", 7): 1, ("yogurt-greek-200", 7): -12}
UNITS_PER_DAY = 300  # expected units sold per day, for the order-up-to levels


def _last_sunday(year, month):
    day = date(year, month, 31)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _offset_hours(day):
    """Greek clock: +3 from the last Sunday of March to the last Sunday of October, else +2."""
    return 3 if _last_sunday(day.year, 3) <= day < _last_sunday(day.year, 10) else 2


def _utc(day, hour, minute, second=0, ms=0):
    """Local Athens time to the schema's UTC string."""
    local = datetime(day.year, day.month, day.day, hour, minute, second)
    return (local - timedelta(hours=_offset_hours(day))).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def athens_now():
    """The current Greek wall-clock time, without tzdata (missing on Windows)."""
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    guess = utc + timedelta(hours=2)
    return utc + timedelta(hours=_offset_hours(guess.date()))


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
                "INSERT OR REPLACE INTO products "
                "(id, name, barcode, category, vat_rate, sell_price_cents, low_threshold, "
                "shelf_life_days) VALUES (?,?,?,?,?,?,?,?)",
                (p["id"], p["name"], p["barcode"], p["category"], p["vat_rate"],
                 p["sell_price_cents"], p["low_threshold"], p.get("shelf_life_days")),
            )
        for s in SUPPLIERS:
            conn.execute(
                "INSERT OR REPLACE INTO suppliers VALUES (?,?,?,?,?,?,?)",
                (s["id"], s["name"], s["afm"], s["email"], s["phone"],
                 json.dumps([s["iban"]]), s["credit_days"]),
            )
        for sp in SUPPLIER_PRODUCTS:
            conn.execute(
                "INSERT OR REPLACE INTO supplier_products "
                "(supplier_id, product_id, supplier_code, pack_size, case_barcode) "
                "VALUES (?,?,?,?,?)",
                (sp["supplier_id"], sp["product_id"], sp["supplier_code"],
                 sp["pack_size"], sp.get("case_barcode")),
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


def seed_history(conn, now):
    """30 days of sales, deliveries and invoices, then the morning state, the open order
    and today's sales up to `now` (Greek wall-clock time)."""
    today = now.date()
    rng = random.Random(SEED)
    total_weight = sum(p["weight"] for p in PRODUCTS)
    rate = {p["id"]: p["weight"] / total_weight * UNITS_PER_DAY for p in PRODUCTS}
    ids = [p["id"] for p in PRODUCTS]
    weights = [p["weight"] for p in PRODUCTS]
    on_hand = {}
    invoice_no = {s["id"]: 100 for s in SUPPLIERS}
    receipt_no = 0
    start = today - timedelta(days=HISTORY_DAYS)

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
                        shift = EXPIRY_SHIFT.get((p["id"], (today - day).days), 0)
                        events.append(
                            _stock_move(_utc(day + timedelta(days=shift), 8, 30), p["id"],
                                        qty, "delivery", unit)
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

    _seed_morning(conn, on_hand, rate, today)
    _seed_today_sales(conn, on_hand, rng, now, receipt_no)


def _seed_morning(conn, on_hand, rate, today):
    """The state of this morning: a count (yesterday 23:59, so today's feed starts
    clean), then the open order P-0318."""
    events = []
    at = _utc(today - timedelta(days=1), 23, 59)
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
            "order.proposed", _utc(today, 6, 45),
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
            "order.sent", _utc(today, 6, 50),
            {"order_id": ORDER_ID, "to": supplier["email"],
             "message_id": f"<{ORDER_ID.lower()}@example.com>"},
            subject=f"order:{ORDER_ID}", idem_key=f"order.sent:{ORDER_ID}",
        )
    )
    append_events(conn, events)


def _seed_today_sales(conn, on_hand, rng, now, receipt_no):
    """Sales from 07:00 until `now`, so "today" on the home screen is not empty.

    Products of the demo story (morning counts and the open order) are not sold,
    and no product drops to its low threshold: the stage starts as the story says.
    """
    today = now.date()
    start = datetime(today.year, today.month, today.day, 7, 0)
    if now <= start:
        return
    protected = set(MORNING_COUNTS) | {pid for pid, _ in ORDER_LINES}
    sellable = [p for p in PRODUCTS if p["id"] not in protected]
    ids = [p["id"] for p in sellable]
    weights = [p["weight"] for p in sellable]
    # Share of the day's receipts that fall between 07:00 and now.
    passed = sum(HOUR_WEIGHTS[7:now.hour]) + HOUR_WEIGHTS[now.hour] * now.minute / 60
    receipts = round(rng.randint(120, 180) * passed / sum(HOUR_WEIGHTS))
    seconds = int((now - start).total_seconds())
    moments = sorted(start + timedelta(seconds=rng.randrange(seconds)) for _ in range(receipts))
    events = []
    for moment in moments:
        chosen = {}
        for _ in range(rng.choices([1, 2, 3], [55, 30, 15])[0]):
            pid = rng.choices(ids, weights)[0]
            room = on_hand[pid] - PRODUCT_BY_ID[pid]["low_threshold"] - 1
            if pid in chosen or room < 1:
                continue
            chosen[pid] = min(rng.choices([1, 2, 3], [78, 17, 5])[0], room)
        if not chosen:
            continue
        receipt_no += 1
        receipt_id = f"R-{receipt_no:06d}"
        at = _utc(today, moment.hour, moment.minute, moment.second, rng.randint(0, 999))
        lines = []
        moves = []
        for pid, qty in chosen.items():
            lines.append({"product_id": pid, "qty": qty,
                          "gross_cents": qty * PRODUCT_BY_ID[pid]["sell_price_cents"]})
            on_hand[pid] -= qty
            moves.append(_stock_move(at, pid, -qty, "sale"))
        payload = {
            "receipt_id": receipt_id,
            "lines": lines,
            "total_cents": sum(line["gross_cents"] for line in lines),
            "payment": rng.choices(["card", "cash"], [62, 38])[0],
        }
        events.append(_event("sale.recorded", at, payload, idem_key=f"sale:{receipt_id}"))
        events.extend(moves)
    if events:
        append_events(conn, events)


def seed_all(conn, now=None):
    """Reference tables plus history. The database must already have its tables.

    `now` is the Greek wall-clock time the demo starts from (default: the real one),
    so "today" on the screens is always the day of the demo.
    """
    seed_reference(conn)
    seed_history(conn, now or athens_now())
