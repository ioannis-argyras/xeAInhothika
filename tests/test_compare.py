import os
import tempfile
import unittest

from xangx.demo.seed import seed_all
from xangx.orders import tools, worker
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.conn = connect(os.path.join(self.dir.name, "t.sqlite"))
        init_db(self.conn)
        seed_all(self.conn)

    def tearDown(self):
        self.conn.close()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def price_changed(self, new, old, invoice_id="inv-x"):
        pct = round((new - old) * 100.0 / old, 1)
        e, _ = append_event(
            self.conn, type="price.changed", actor="invoices",
            payload={"product_id": "cola-330", "supplier_id": "sup-ymittou",
                     "old_cents": old, "new_cents": new, "pct": pct},
            subject="product:cola-330", idem_key=f"price:{invoice_id}:cola-330",
        )
        return e

    def found(self):
        return read_events(self.conn, types=["supplier.cheaper_found"])

    def test_rise_finds_cheaper_supplier_once(self):
        prices = {i: c for i, _, c in tools.supplier_prices(self.conn, "cola-330")}
        self.assertIn("sup-kava", prices)
        self.assertEqual(prices["sup-kava"], 58)
        self.price_changed(62, 57)
        self.assertEqual(worker.run_once(self.conn), 1)
        [f] = self.found()
        p = f.payload
        self.assertEqual((p["from_supplier_id"], p["to_supplier_id"]), ("sup-ymittou", "sup-kava"))
        self.assertEqual((p["from_cents"], p["to_cents"]), (62, 58))
        self.assertEqual(f.actor, "orders")
        # Same event again (even with the cursor lost): nothing new.
        e = read_events(self.conn, types=["price.changed"])[-1]
        worker.cheaper_for(self.conn, e)
        self.assertEqual(len(self.found()), 1)

    def test_drop_finds_nothing(self):
        self.price_changed(50, 60)
        worker.run_once(self.conn)
        self.assertEqual(self.found(), [])

    def test_no_cheaper_supplier_finds_nothing(self):
        self.price_changed(1, 1)  # pct 0 and nothing lower than 1 cent
        worker.run_once(self.conn)
        self.assertEqual(self.found(), [])

    def test_fresh_seed_shows_kava_under_the_raised_price(self):
        out = tools.compare_suppliers(self.conn, "cola-330")
        by = {s["supplier_id"]: s["unit_cents"] for s in out["suppliers"]}
        self.assertEqual((by["sup-kava"], by["sup-ymittou"]), (58, 57))
        self.price_changed(62, 57, invoice_id="A-2271")
        worker.run_once(self.conn)
        [f] = self.found()
        self.assertEqual((f.payload["to_supplier_id"], f.payload["to_cents"]), ("sup-kava", 58))

    def test_compare_suppliers_tool(self):
        out = tools.compare_suppliers(self.conn, "cola-330")
        self.assertIn("Coca-Cola 330ml", out["summary"])
        self.assertIn("Κάβα Βύρωνα", out["summary"])
        self.assertEqual(out["event_ids"], [])
        self.assertGreaterEqual(len(out["suppliers"]), 2)
        self.assertEqual(tools.compare_suppliers(self.conn, "nope")["event_ids"], [])


if __name__ == "__main__":
    unittest.main()
