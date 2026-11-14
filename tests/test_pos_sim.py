import contextlib
import importlib.util
import io
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xangx.demo.seed import seed_all
from xangx.shared import views
from xangx.shared.db import connect, init_db
from xangx.shared.events import read_events

_spec = importlib.util.spec_from_file_location(
    "pos_sim", Path(__file__).resolve().parent.parent / "scripts" / "pos_sim.py")
pos_sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pos_sim)


class PosSimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory()
        cls.seed_db = os.path.join(cls.seed_dir.name, "seed.sqlite")
        conn = connect(cls.seed_db)
        init_db(conn)
        seed_all(conn)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.seed_dir.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        src, dst = connect(self.seed_db), connect(self.db)
        src.backup(dst)
        src.close()
        dst.close()
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def sales(self):
        return read_events(self.conn, types=["sale.recorded"])

    def test_receipt_continues_and_moves_stock(self):
        top = max(e.payload["receipt_id"] for e in self.sales())
        before = views.stock_levels(self.conn)
        rng, sale = random.Random(7), None
        for _ in range(50):
            sale = sale or pos_sim.sell_one(self.conn, rng)
        self.assertIsNotNone(sale)
        self.assertEqual(len([e for e in self.sales() if e.actor == "pos-sim"]), 1)
        self.assertEqual(sale["receipt_id"], f"R-{int(top[2:]) + 1:06d}")
        event = self.sales()[-1]
        self.assertEqual((event.actor, event.idem_key), ("pos-sim", f"sale:{sale['receipt_id']}"))
        self.assertEqual(sale["total_cents"], sum(l["gross_cents"] for l in sale["lines"]))
        after = views.stock_levels(self.conn)
        for l in sale["lines"]:
            self.assertEqual(after[l["product_id"]], before[l["product_id"]] - l["qty"])
        moves = [e for e in read_events(self.conn, types=["stock.moved"])
                 if (e.idem_key or "").startswith(f"sale:{sale['receipt_id']}:")]
        self.assertEqual(len(moves), len(sale["lines"]))
        self.assertTrue(all(e.payload["reason"] == "sale" and e.actor == "pos-sim" for e in moves))

    def test_skip_and_never_down_to_low(self):
        low = {r["id"]: r["low_threshold"] for r in self.conn.execute("SELECT id, low_threshold FROM products")}
        start = views.stock_levels(self.conn)
        rng = random.Random(3)
        sold = set()
        for _ in range(300):
            sale = pos_sim.sell_one(self.conn, rng, skip={"cola-330"})
            if sale:
                sold.update(l["product_id"] for l in sale["lines"])
        self.assertNotIn("cola-330", sold)
        self.assertGreater(len(sold), 10)
        end = views.stock_levels(self.conn)
        for pid, qty in end.items():
            if start.get(pid, 0) > low[pid]:
                self.assertGreater(qty, low[pid], pid)
            else:
                self.assertEqual(qty, start[pid], pid)  # already low: not sold at all

    def test_main_runs_a_few_receipts_fast(self):
        n = len(self.sales())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            pos_sim.main(["--count", "3", "--speed", "1000000", "--seed", "1", "--skip", "cola-330,sprite-330"])
        self.assertEqual(len(self.sales()), n + 3)
        self.assertIn("δεν πουλά: cola-330, sprite-330", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pos_sim.main(["--skip", "no-such-product", "--count", "1"])

    def test_rate_follows_the_hour(self):
        self.assertGreater(pos_sim.receipts_per_hour(19), pos_sim.receipts_per_hour(4) * 10)
        day = sum(pos_sim.receipts_per_hour(h) for h in range(24))
        self.assertAlmostEqual(day, pos_sim.RECEIPTS_PER_DAY)


if __name__ == "__main__":
    unittest.main()
