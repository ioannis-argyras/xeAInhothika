import os
import tempfile
import unittest
from unittest import mock

from xangx.demo.seed import seed_all
from xangx.master import agent
from xangx.orders import tools
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events
from xangx.stock import worker


class OrdersTest(unittest.TestCase):
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
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SMTP_") and k != "XANGX_DEMO_SUPPLIER_EMAIL"}
        env.update({"XANGX_DB": self.db, "XANGX_FILES": os.path.join(self.dir.name, "files"),
                    "XANGX_FAKE_LLM": "1"})
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.conn = connect(self.db)
        # The shelf camera saw 5 Coca-Cola taken: the stock worker flags it low today.
        append_event(self.conn, type="shelf.items_taken", actor="camera",
                     payload={"position_id": "fridge1-r1", "product_id": "cola-330", "taken": 5,
                              "confidence": "high", "file_ref": "b.jpg", "prev_file_ref": "a.jpg"})
        worker.run_once(self.conn)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def events(self, type):
        return read_events(self.conn, types=[type])

    def ask_and_approve(self, order_id):
        append_event(self.conn, type="approval.requested", actor="master",
                     payload={"approval_id": "A-T1", "action": "send_order", "summary": "s",
                              "exact": "e", "why": "w", "ref": order_id, "tool": "send_order",
                              "args": {"order_id": order_id}})
        return agent.decide_approval(self.conn, "A-T1", "yes")

    def test_proposal_rounds_up_to_pack_size(self):
        r = tools.propose_order(self.conn, "sup-ymittou")
        self.assertEqual(r["order_id"], "P-0319")
        self.assertTrue(r["summary"].startswith("Ετοίμασα παραγγελία P-0319: "))
        p = self.events("order.proposed")[-1].payload
        packs = {row["product_id"]: row["pack_size"] for row in self.conn.execute(
            "SELECT product_id, pack_size FROM supplier_products WHERE supplier_id = 'sup-ymittou'")}
        lines = {l["product_id"]: l for l in p["lines"]}
        self.assertIn("cola-330", lines)
        for pid, l in lines.items():
            self.assertGreater(l["qty"], 0)
            self.assertEqual(l["qty"] % packs[pid], 0, pid)
            self.assertIsInstance(l["unit_cents"], int)
        self.assertEqual(p["net_cents"], sum(l["qty"] * l["unit_cents"] for l in p["lines"]))
        self.assertEqual(set(p["why"]), {"per_day", "on_hand", "lead_days"})

    def test_same_proposal_twice_is_not_duplicated(self):
        first = tools.propose_order(self.conn, "sup-ymittou")
        second = tools.propose_order(self.conn, "sup-ymittou")
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertEqual(len([e for e in self.events("order.proposed")
                              if e.payload["order_id"] == "P-0319"]), 1)

    def test_unknown_supplier_lists_the_ones_with_low_products(self):
        r = tools.propose_order(self.conn, "")
        self.assertEqual(r["event_ids"], [])
        self.assertIn("sup-ymittou", [s["supplier_id"] for s in r["suppliers_with_low"]])

    def test_send_needs_yes(self):
        order_id = tools.propose_order(self.conn, "sup-ymittou")["order_id"]
        r = tools.send_order(self.conn, order_id)
        self.assertIn("«ναι»", r["summary"])
        self.assertFalse([e for e in self.events("order.sent") if e.payload["order_id"] == order_id])

    def test_send_after_yes_once_only_to_outbox(self):
        order_id = tools.propose_order(self.conn, "sup-ymittou")["order_id"]
        info = tools.describe_send({"order_id": order_id})
        self.assertIn("Διανομές Υμηττού", info["summary"])
        self.assertIn("Coca-Cola", info["exact"])
        self.assertTrue(info["why"].startswith("Πουλάς "))

        result = self.ask_and_approve(order_id)
        self.assertTrue(result["ok"], result)
        self.assertIn("Στάλθηκε η παραγγελία", result["message"])
        sent = [e for e in self.events("order.sent") if e.payload["order_id"] == order_id]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].payload["to"], "paraggelies.ymittou@example.com")
        decided = self.events("approval.decided")[-1]
        self.assertEqual(sent[0].caused_by, decided.id)
        eml = os.path.join(self.dir.name, "outbox", f"{order_id}.eml")
        self.assertTrue(os.path.exists(eml))
        mtime = os.path.getmtime(eml)

        again = tools.send_order(self.conn, order_id)
        self.assertIn("ήδη σταλεί", again["summary"])
        self.assertEqual(len([e for e in self.events("order.sent")
                              if e.payload["order_id"] == order_id]), 1)
        self.assertEqual(os.path.getmtime(eml), mtime)

    def test_demo_address_wins(self):
        order_id = tools.propose_order(self.conn, "sup-ymittou")["order_id"]
        with mock.patch.dict(os.environ, {"XANGX_DEMO_SUPPLIER_EMAIL": "supplier-demo@example.com"}):
            self.ask_and_approve(order_id)
        sent = [e for e in self.events("order.sent") if e.payload["order_id"] == order_id]
        self.assertEqual(sent[0].payload["to"], "supplier-demo@example.com")


if __name__ == "__main__":
    unittest.main()
