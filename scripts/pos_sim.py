"""Till simulator: sells from the demo shop at the hour's rate, like the 30-day history.

Run from the repo root (server or not, it writes straight to the database):
    python scripts/pos_sim.py --speed 10 --skip cola-330

Each receipt writes sale.recorded and one stock.moved (reason "sale") per product,
actor pos-sim, idem_key sale:<receipt_id>. A product is never sold down to its low
threshold, so the simulator does not make the "low" of the demo story by itself.
Receipt ids continue from the largest one in the database. Stop with Ctrl-C.
"""

import argparse
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xangx.demo.catalog_data import PRODUCTS  # noqa: E402
from xangx.demo.seed import HOUR_WEIGHTS  # noqa: E402
from xangx.master.tools import greek_offset  # noqa: E402
from xangx.shared import views  # noqa: E402
from xangx.shared.db import connect, init_db  # noqa: E402
from xangx.shared.events import append_events  # noqa: E402
from xangx.shared.money import format_eur  # noqa: E402

ACTOR = "pos-sim"
RECEIPTS_PER_DAY = 150  # the history has 120-180 a day
WEIGHT = {p["id"]: p["weight"] for p in PRODUCTS}


def receipts_per_hour(hour):
    return RECEIPTS_PER_DAY * HOUR_WEIGHTS[hour] / sum(HOUR_WEIGHTS)


def greek_hour(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + greek_offset(now)).hour


def next_receipt_id(conn):
    row = conn.execute(
        "SELECT MAX(json_extract(payload, '$.receipt_id')) AS top FROM events "
        "WHERE type = 'sale.recorded'").fetchone()
    top = row["top"] or "R-000000"
    return f"R-{int(top.split('-')[1]) + 1:06d}"


def sell_one(conn, rng, skip=()):
    """Write one receipt. Returns the sale.recorded payload, or None if nothing can be sold."""
    levels = views.stock_levels(conn)
    rows = [r for r in conn.execute(
        "SELECT id, sell_price_cents, low_threshold FROM products "
        "WHERE sell_price_cents IS NOT NULL ORDER BY id") if r["id"] not in skip]
    if not rows:
        return None
    by_id = {r["id"]: r for r in rows}
    ids = [r["id"] for r in rows]
    weights = [WEIGHT.get(pid, 1.0) for pid in ids]
    chosen = {}
    for _ in range(rng.choices([1, 2, 3], [55, 30, 15])[0]):
        pid = rng.choices(ids, weights)[0]
        room = (levels.get(pid, 0) or 0) - (by_id[pid]["low_threshold"] or 0) - 1
        if pid in chosen or room < 1:
            continue
        chosen[pid] = min(rng.choices([1, 2, 3], [78, 17, 5])[0], room)
    if not chosen:
        return None
    receipt_id = next_receipt_id(conn)
    lines = [{"product_id": pid, "qty": qty, "gross_cents": qty * by_id[pid]["sell_price_cents"]}
             for pid, qty in chosen.items()]
    payload = {"receipt_id": receipt_id, "lines": lines,
               "total_cents": sum(l["gross_cents"] for l in lines),
               "payment": rng.choices(["card", "cash"], [62, 38])[0]}
    items = [{"type": "sale.recorded", "actor": ACTOR, "payload": payload,
              "subject": f"receipt:{receipt_id}", "idem_key": f"sale:{receipt_id}"}]
    for l in lines:
        items.append({"type": "stock.moved", "actor": ACTOR, "subject": f"product:{l['product_id']}",
                      "idem_key": f"sale:{receipt_id}:{l['product_id']}",
                      "payload": {"product_id": l["product_id"], "qty": -l["qty"], "reason": "sale"}})
    append_events(conn, items)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="Till simulator for the demo shop.")
    ap.add_argument("--speed", type=float, default=1.0, help="times faster than real life (stage: 10)")
    ap.add_argument("--skip", action="append", default=[],
                    help="product id not to sell, e.g. cola-330 (repeat, or comma separated)")
    ap.add_argument("--count", type=int, default=0, help="stop after this many receipts (0: never)")
    ap.add_argument("--seed", type=int, default=None, help="random seed, for repeatable runs")
    args = ap.parse_args(argv)
    if args.speed <= 0:
        ap.error("--speed must be above 0")
    skip = {s.strip() for item in args.skip for s in item.split(",") if s.strip()}

    rng = random.Random(args.seed)
    conn = connect()
    init_db(conn)
    known = {r["id"] for r in conn.execute("SELECT id FROM products")}
    unknown = sorted(skip - known)
    if unknown:
        ap.error(f"unknown product id in --skip: {', '.join(unknown)}")
    print(f"Ταμείο: ×{args.speed:g}, δεν πουλά: {', '.join(sorted(skip)) or 'τίποτα'}. Ctrl-C για στάση.",
          flush=True)
    done = 0
    try:
        while not args.count or done < args.count:
            per_second = receipts_per_hour(greek_hour()) * args.speed / 3600
            time.sleep(min(rng.expovariate(per_second), 600))
            sale = sell_one(conn, rng, skip)
            if sale is None:
                continue
            done += 1
            items = ", ".join(f"{l['qty']} {l['product_id']}" for l in sale["lines"])
            print(f"{sale['receipt_id']}: {items} · {format_eur(sale['total_cents'])}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
    print(f"Τέλος: {done} αποδείξεις.")


if __name__ == "__main__":
    main()
