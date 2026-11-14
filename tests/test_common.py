import os
import tempfile
import unittest
from pathlib import Path

from xangx.shared import append_event, append_events, connect, init_db
from xangx.shared.env import load_env
from xangx.shared.views import stock_levels, stock_on_hand


class SystemActorTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)

    def test_system_may_write_any_type_with_at(self):
        event, created = append_event(
            self.conn, type="sale.recorded", actor="system",
            at="2026-10-01T10:00:00.000Z",
            payload={"receipt_id": "R-1", "lines": [], "total_cents": 0,
                     "payment": "cash"},
        )
        self.assertTrue(created)
        self.assertEqual(event.actor, "system")
        self.assertEqual(event.at, "2026-10-01T10:00:00.000Z")

    def test_other_actor_still_restricted(self):
        from xangx.shared import EventError
        with self.assertRaises(EventError):
            append_event(
                self.conn, type="sale.recorded", actor="owner",
                payload={"receipt_id": "R-1", "lines": [], "total_cents": 0,
                         "payment": "cash"},
            )


class StockLevelsTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)

    def test_levels_match_stock_on_hand_and_until_id(self):
        items = [
            dict(type="stock.moved", actor="stock",
                 payload={"product_id": pid, "qty": qty, "reason": "count"})
            for pid, qty in [("a", 10), ("b", 5), ("a", -3), ("b", 2)]
        ]
        results = append_events(self.conn, items)
        levels = stock_levels(self.conn)
        self.assertEqual(levels, {"a": 7, "b": 7})
        self.assertEqual(levels["a"], stock_on_hand(self.conn, "a"))
        first_two = results[1][0].id
        self.assertEqual(stock_levels(self.conn, until_id=first_two), {"a": 10, "b": 5})

    def test_empty(self):
        self.assertEqual(stock_levels(self.conn), {})


class EnvTests(unittest.TestCase):
    def test_load_env(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                "# comment\n\nXTEST_A=one\nXTEST_B = \"two words\"\n"
                "XTEST_KEEP=new\nnot a line\nXTEST_EMPTY=\n",
                encoding="utf-8", newline="\n",
            )
            for name in ("XTEST_A", "XTEST_B", "XTEST_EMPTY"):
                os.environ.pop(name, None)
            os.environ["XTEST_KEEP"] = "old"
            try:
                load_env(str(path))
                self.assertEqual(os.environ["XTEST_A"], "one")
                self.assertEqual(os.environ["XTEST_B"], "two words")
                self.assertEqual(os.environ["XTEST_KEEP"], "old")
                self.assertEqual(os.environ["XTEST_EMPTY"], "")
            finally:
                for name in ("XTEST_A", "XTEST_B", "XTEST_KEEP", "XTEST_EMPTY"):
                    os.environ.pop(name, None)

    def test_missing_file(self):
        self.assertEqual(load_env("definitely-missing.env"), [])


if __name__ == "__main__":
    unittest.main()
