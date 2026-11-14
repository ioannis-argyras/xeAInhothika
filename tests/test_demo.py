import json
import unittest
from datetime import datetime

from xangx.demo.catalog_data import (
    POSITIONS,
    PRODUCTS,
    SUPPLIERS,
    afm_valid,
    iban_valid,
)
from xangx.demo.seed import seed_all
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
        self.assertEqual(len(PRODUCTS), 35)
        self.assertEqual(len(SUPPLIERS), 4)
        self.assertEqual(len(POSITIONS), 31)
        fridge = [p for p in POSITIONS if p["camera_id"] == "cam-1"]
        self.assertEqual(len(fridge), 6)
        self.assertIn("cola-330", [p["product_id"] for p in fridge])
        for table, expected in (("products", 35), ("suppliers", 4), ("positions", 31)):
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, expected, table)

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
