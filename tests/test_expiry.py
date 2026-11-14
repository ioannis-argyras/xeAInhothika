import os
import tempfile
import unittest
from datetime import date, datetime, timezone

from xangx.demo.seed import athens_now, seed_all
from xangx.master.tools import TOOLS
from xangx.screen.describe import describe
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events
from xangx.stock import expiry, tools as stock_tools, worker  # noqa: F401  (registers the tools)
from xangx.stock.expiry import _greek_day

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def move(conn, product_id, qty, day, reason="delivery"):
    append_event(conn, type="stock.moved", actor="system", subject=f"product:{product_id}",
                 at=f"{day}T08:30:00.000Z",
                 payload={"product_id": product_id, "qty": qty, "reason": reason})


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.conn = connect(os.path.join(self.dir.name, "t.sqlite"))
        init_db(self.conn)
        for pid, name, life in (("milk", "Γάλα 1L", 7), ("rice", "Ρύζι", None), ("yog", "Γιαούρτι", 21)):
            self.conn.execute(
                "INSERT INTO products (id, name, vat_rate, sell_price_cents, low_threshold, shelf_life_days) "
                "VALUES (?,?,13,100,1,?)", (pid, name, life))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_oldest_goes_first(self):
        move(self.conn, "milk", 10, "2026-11-07")   # expires 14/11
        move(self.conn, "milk", 10, "2026-11-12")   # expires 19/11
        move(self.conn, "milk", -12, "2026-11-13", "sale")  # 10 from the old, 2 from the new
        self.assertEqual(expiry.expiring(self.conn, 3, now=NOW), [])
        left = expiry.batches(self.conn)["milk"]
        self.assertEqual([q for _, q in left], [8])
        move(self.conn, "milk", -3, "2026-11-13", "sale")
        self.assertEqual([q for _, q in expiry.batches(self.conn)["milk"]], [5])

    def test_partly_sold_old_batch_is_listed(self):
        move(self.conn, "milk", 10, "2026-11-08")   # expires 15/11 = tomorrow
        move(self.conn, "milk", 10, "2026-11-13")
        move(self.conn, "milk", -4, "2026-11-13", "sale")
        items = expiry.expiring(self.conn, 3, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["product_id"], items[0]["qty"], items[0]["expires_on"], items[0]["days_left"]),
                         ("milk", 6, "2026-11-15", 1))

    def test_expired_is_listed_and_null_never_expires(self):
        move(self.conn, "milk", 5, "2026-11-01")
        move(self.conn, "rice", 50, "2020-01-01")
        items = expiry.expiring(self.conn, 3, now=NOW)
        self.assertEqual([i["product_id"] for i in items], ["milk"])
        self.assertEqual(items[0]["days_left"], -6)

    def test_worker_writes_once_per_batch_and_not_expired(self):
        move(self.conn, "milk", 3, "2026-11-01")    # already expired: no event
        move(self.conn, "milk", 6, "2026-11-08")    # tomorrow
        move(self.conn, "yog", 4, "2026-10-26")     # in 2 days
        move(self.conn, "milk", -3, "2026-11-09", "sale")  # takes the oldest first
        first = worker.check_expiring(self.conn, now=NOW)
        second = worker.check_expiring(self.conn, now=NOW)
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        events = read_events(self.conn, types=["stock.expiring"])
        self.assertEqual(sorted(e.idem_key for e in events),
                         ["expiring:milk:2026-11-15", "expiring:yog:2026-11-16"])
        self.assertEqual(events[0].actor, "stock")
        # the event's `at` is the real clock, so count the days from its own Greek day
        left = (date(2026, 11, 15) - _greek_day(events[0].at)).days
        when = {0: "Λήγει σήμερα", 1: "Λήγει αύριο"}.get(left, f"Λήγει σε {left} μέρες")
        self.assertEqual(describe(events[0], {"product": {"milk": "Γάλα 1L"}}), f"{when}: 6 Γάλα 1L")

    def test_tool(self):
        move(self.conn, "milk", 6, "2026-11-08")
        move(self.conn, "yog", 4, "2026-10-26")
        # the tool uses the real clock, so build the batches relative to today
        out = TOOLS["expiring_soon"].fn(self.conn, within_days=3)
        self.assertIn("items", out)
        self.assertEqual(out["event_ids"], [])
        text = expiry.summary_text(expiry.expiring(self.conn, 3, now=NOW), 3)
        self.assertEqual(text, "Λήγουν αύριο: 6 Γάλα 1L· σε 2 μέρες: 4 Γιαούρτι")
        self.assertEqual(expiry.summary_text([], 3), "Δεν λήγει τίποτα τις επόμενες 3 μέρες.")
        self.assertFalse(TOOLS["expiring_soon"].needs_approval)


class DemoExpiryTest(unittest.TestCase):
    def test_seed_has_milk_tomorrow_and_yogurt_in_two_days(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect(os.path.join(d, "seed.sqlite"))
            init_db(conn)
            seed_all(conn)
            now = datetime.now(timezone.utc)
            items = {i["product_id"]: i for i in expiry.expiring(conn, 3, now=now)}
            conn.close()
        self.assertEqual(items["milk-fresh-1l"]["days_left"], 1)
        self.assertEqual(items["yogurt-greek-200"]["days_left"], 2)
        self.assertGreater(items["milk-fresh-1l"]["qty"], 0)


if __name__ == "__main__":
    unittest.main()
