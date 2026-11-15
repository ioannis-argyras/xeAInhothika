import json
import unittest
from datetime import datetime, timezone

from xangx.demo.catalog_data import (
    POSITIONS,
    PRODUCTS,
    SUPPLIERS,
    afm_valid,
    ean13,
    iban_valid,
)
from xangx.accountant.trends import purchase_trends
from xangx.demo.catalog_data import SUPPLIER_PRICE
from xangx.demo.seed import ORDER_LINES, PRICE_HISTORY, seed_all
from xangx.shared import connect, init_db, read_events
from xangx.shared.views import (
    last_unit_price_cents,
    order_status,
    stock_levels,
)


class DemoSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = connect(":memory:")
        init_db(cls.conn)
        seed_all(cls.conn, now=datetime(2026, 11, 14, 6, 55))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_catalog_sizes(self):
        self.assertEqual(len(PRODUCTS), 36)
        self.assertEqual(len(SUPPLIERS), 4)
        self.assertEqual(len(POSITIONS), 32)
        fridge = [p for p in POSITIONS if p["camera_id"] == "cam-1"]
        self.assertEqual(
            [p["product_id"] for p in fridge],
            ["coffee-frappe-can", "coffee-ground-194", "coffee-espresso-b", "water-500"],
        )
        for table, expected in (("products", 36), ("suppliers", 4), ("positions", 32)):
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, expected, table)

    def test_fridge_products_are_at_3_and_not_low_in_the_morning(self):
        levels = stock_levels(self.conn)
        by_id = {p["id"]: p for p in PRODUCTS}
        for pid in ("coffee-frappe-can", "coffee-ground-194", "coffee-espresso-b", "water-500"):
            self.assertEqual(levels[pid], 3, pid)
            self.assertEqual(by_id[pid]["low_threshold"], 1, pid)
        self.assertEqual(by_id["coffee-frappe-can"]["name"], "Cold brew κουτάκι 250ml")
        self.assertEqual(by_id["coffee-ground-194"]["name"], "Espresso κόκκοι, ποικιλία Α")
        self.assertEqual(by_id["coffee-espresso-b"]["name"], "Espresso κόκκοι, ποικιλία Β")

    def _invoice_prices(self):
        """(supplier id, product id) -> unit prices of the invoices, oldest first."""
        sup = {r["afm"]: r["id"] for r in self.conn.execute("SELECT id, afm FROM suppliers")}
        prices = {}
        for e in read_events(self.conn, types=["invoice.read"]):
            for line in e.payload["lines"]:
                key = (sup[e.payload["supplier_afm"]], line["product_id"])
                prices.setdefault(key, []).append(line["unit_cents"])
        return prices

    def test_price_history_old_then_current_price(self):
        self.assertEqual(len(PRICE_HISTORY), 7)
        prices = self._invoice_prices()
        for key, (old, _days) in PRICE_HISTORY.items():
            self.assertIn(old, prices[key], key)
            self.assertIn(SUPPLIER_PRICE[key], prices[key], key)
            self.assertEqual(prices[key][0], old, key)
            self.assertEqual(prices[key][-1], SUPPLIER_PRICE[key], key)
            self.assertEqual(sorted(set(prices[key]), key=prices[key].index), [old, SUPPLIER_PRICE[key]], key)

    def test_latest_price_unchanged_everywhere_else(self):
        for key, series in self._invoice_prices().items():
            if key in PRICE_HISTORY:
                continue
            expected = 58 if key == ("sup-kava", "cola-330") else SUPPLIER_PRICE[key]
            self.assertEqual(series[-1], expected, key)
        self.assertEqual(last_unit_price_cents(self.conn, "cola-330"), 57)
        self.assertEqual(self._invoice_prices()[("sup-kava", "cola-330")], [58])

    def test_no_price_changed_events_in_the_history(self):
        self.assertEqual(read_events(self.conn, types=["price.changed"]), [])

    def test_five_rises_and_two_falls_of_3_to_9_percent(self):
        ups = downs = 0
        for key, (old, _days) in PRICE_HISTORY.items():
            pct = round((SUPPLIER_PRICE[key] - old) * 100.0 / old, 1)
            self.assertTrue(3 <= abs(pct) <= 9, (key, pct))
            ups += pct > 0
            downs += pct < 0
        self.assertEqual((ups, downs), (5, 2))

    def test_purchase_trends_show_the_seven_changes(self):
        trends = purchase_trends(self.conn, now=datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(len(trends["changes"]), 7)
        self.assertEqual({c["product_id"] for c in trends["changes"]},
                         {pid for _sup, pid in PRICE_HISTORY})

    def test_no_order_line_product_changes_price(self):
        history_products = {pid for _sup, pid in PRICE_HISTORY}
        self.assertFalse(history_products & {pid for pid, _qty in ORDER_LINES})
        self.assertNotIn("cola-330", history_products)

    def test_cola_on_hand_is_8(self):
        self.assertEqual(stock_levels(self.conn)["cola-330"], 8)

    def test_many_sales(self):
        count = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='sale.recorded'"
        ).fetchone()[0]
        self.assertGreater(count, 3000)

    def test_open_order(self):
        self.assertEqual(order_status(self.conn, "P-0318"), "order.sent")
        proposed = read_events(self.conn, types=["order.proposed"])[-1]
        self.assertEqual(
            [line["qty"] for line in proposed.payload["lines"]], [48, 10, 24, 30, 12]
        )

    def test_cola_price_and_low_products(self):
        self.assertEqual(last_unit_price_cents(self.conn, "cola-330"), 57)
        levels = stock_levels(self.conn)
        low = [p["id"] for p in PRODUCTS if levels.get(p["id"], 0) <= p["low_threshold"]]
        self.assertIn("chips-salt-90", low)
        self.assertLessEqual(len(low), 3)
        self.assertTrue(all(level >= 0 for level in levels.values()))

    def test_case_barcodes_of_the_open_order(self):
        rows = self.conn.execute(
            "SELECT product_id, case_barcode FROM supplier_products "
            "WHERE case_barcode IS NOT NULL AND supplier_id = 'sup-ymittou'"
        ).fetchall()
        self.assertEqual(
            sorted(r["product_id"] for r in rows),
            sorted(["cola-330", "water-1500x6", "chips-salt-90", "choco-milk-100",
                    "juice-orange-1l"]),
        )
        codes = [r["case_barcode"] for r in rows]
        self.assertEqual(len(set(codes)), 5)
        product_codes = {p["barcode"] for p in PRODUCTS}
        for code in codes:
            self.assertEqual(len(code), 13)
            self.assertTrue(code.startswith("200"))
            self.assertEqual(code, ean13(code[:12]))
            self.assertNotIn(code, product_codes)
        total = self.conn.execute(
            "SELECT COUNT(*) FROM supplier_products WHERE case_barcode IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(total, 5)

    def test_init_db_adds_case_barcode_to_an_old_database(self):
        old = connect(":memory:")
        old.executescript(
            "CREATE TABLE supplier_products (supplier_id TEXT NOT NULL, "
            "product_id TEXT NOT NULL, supplier_code TEXT, pack_size INTEGER, "
            "PRIMARY KEY (supplier_id, product_id));"
        )
        init_db(old)
        init_db(old)
        names = [r[1] for r in old.execute("PRAGMA table_info(supplier_products)")]
        self.assertEqual(names.count("case_barcode"), 1)

    def test_afm_and_iban_valid(self):
        for supplier in SUPPLIERS:
            self.assertTrue(afm_valid(supplier["afm"]), supplier["name"])
            self.assertTrue(iban_valid(supplier["iban"]), supplier["name"])
        stored = self.conn.execute("SELECT afm, known_ibans FROM suppliers").fetchall()
        for row in stored:
            self.assertTrue(afm_valid(row["afm"]))
            for iban in json.loads(row["known_ibans"]):
                self.assertTrue(iban_valid(iban))
        self.assertFalse(afm_valid("990000019"))
        self.assertFalse(iban_valid("GR0000000000000000000000000"))

    def test_morning_count_is_not_in_todays_feed(self):
        day_start = "2026-11-13T22:00:00.000Z"
        count = self.conn.execute(
            "SELECT at FROM events WHERE type='stock.moved' "
            "AND json_extract(payload, '$.reason') = 'count' "
            "AND at >= '2026-11-13T21:00:00.000Z' ORDER BY at"
        ).fetchall()
        self.assertTrue(count)
        self.assertTrue(all(r["at"] < day_start for r in count))
        self.assertEqual(stock_levels(self.conn)["cola-330"], 8)

    def test_history_is_system_and_in_the_past(self):
        row = self.conn.execute(
            "SELECT MIN(at) AS first, MAX(at) AS last, COUNT(DISTINCT actor) AS actors "
            "FROM events"
        ).fetchone()
        self.assertEqual(row["actors"], 1)
        self.assertTrue(row["first"].startswith("2026-10-14"))
        self.assertTrue(row["last"].startswith("2026-11-14"))


class DemoTodayTests(unittest.TestCase):
    """Seeded in the afternoon: today has sales, the story's products are untouched."""

    @classmethod
    def setUpClass(cls):
        cls.conn = connect(":memory:")
        init_db(cls.conn)
        seed_all(cls.conn, now=datetime(2026, 11, 15, 17, 30))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_sales_today_and_story_intact(self):
        today = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='sale.recorded' AND at >= '2026-11-14T22:00'"
        ).fetchone()[0]
        self.assertGreater(today, 40)
        last = self.conn.execute("SELECT MAX(at) FROM events").fetchone()[0]
        self.assertLess(last, "2026-11-15T15:30")
        levels = stock_levels(self.conn)
        self.assertEqual(levels["cola-330"], 8)
        low = [p["id"] for p in PRODUCTS if levels.get(p["id"], 0) <= p["low_threshold"]]
        self.assertLessEqual(len(low), 3)
        self.assertEqual(order_status(self.conn, "P-0318"), "order.sent")


if __name__ == "__main__":
    unittest.main()
