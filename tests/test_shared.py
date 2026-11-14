import re
import sqlite3
import unittest
from pathlib import Path

from xangx.shared import EventError, append_event, connect, init_db, read_events
from xangx.shared.catalog import CATALOG
from xangx.shared.cursors import consume, get_cursor
from xangx.shared.money import format_eur, line_net_cents, vat_cents
from xangx.shared.views import pending_approvals, stock_on_hand

SCHEMA_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "SXIMA-GEGONOTON.md"
)


def new_db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def move(conn, qty, product_id="product:cola-330"):
    return append_event(
        conn,
        type="stock.moved",
        actor="stock",
        payload={"product_id": product_id, "qty": qty, "reason": "count"},
    )[0]


def approval_payload(approval_id):
    return {
        "approval_id": approval_id,
        "action": "send_order",
        "summary": "s",
        "exact": "e",
        "why": "w",
        "ref": "order:P-1",
    }


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()

    def tearDown(self):
        self.conn.close()

    def test_01_write_and_read(self):
        event, created = append_event(
            self.conn,
            type="owner.said",
            actor="owner",
            payload={"text": "γειά", "channel": "web"},
        )
        self.assertTrue(created)
        self.assertRegex(event.at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        events = read_events(self.conn)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["text"], "γειά")
        self.assertEqual(events[0], event)

    def test_02_unknown_type(self):
        with self.assertRaises(EventError):
            append_event(self.conn, type="nope.nothing", actor="owner", payload={})

    def test_03_actor_not_allowed(self):
        with self.assertRaises(EventError):
            append_event(
                self.conn,
                type="order.sent",
                actor="camera",
                payload={"order_id": "P-1", "to": "a@b.c", "message_id": "m"},
            )

    def test_04_missing_required_field(self):
        with self.assertRaises(EventError) as ctx:
            append_event(
                self.conn,
                type="owner.said",
                actor="owner",
                payload={"text": "hi"},
            )
        self.assertIn("channel", str(ctx.exception))

    def test_05_idem_key(self):
        kwargs = dict(
            type="owner.said",
            actor="owner",
            payload={"text": "hi", "channel": "web"},
            idem_key="k1",
        )
        first, created1 = append_event(self.conn, **kwargs)
        second, created2 = append_event(self.conn, **kwargs)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(read_events(self.conn)), 1)

    def test_06_append_only(self):
        move(self.conn, 1)
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE events SET actor = 'x'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM events")
        self.assertEqual(len(read_events(self.conn)), 1)

    def test_07_consume(self):
        ids = [move(self.conn, q).id for q in (1, 2, 3)]
        seen = []

        def handler(event):
            if event.payload["qty"] == 3:
                raise RuntimeError("boom")
            seen.append(event.id)

        with self.assertRaises(RuntimeError):
            consume(self.conn, "stock", ["stock.moved"], handler)
        self.assertEqual(seen, ids[:2])
        self.assertEqual(get_cursor(self.conn, "stock"), ids[1])

        seen.clear()
        count = consume(self.conn, "stock", ["stock.moved"], lambda e: seen.append(e.id))
        self.assertEqual(count, 1)
        self.assertEqual(seen, [ids[2]])
        self.assertEqual(get_cursor(self.conn, "stock"), ids[2])

    def test_08_stock_on_hand(self):
        move(self.conn, 10)
        middle = move(self.conn, -3)
        move(self.conn, 5)
        move(self.conn, 99, product_id="product:other")
        self.assertEqual(stock_on_hand(self.conn, "product:cola-330", until_id=middle.id), 7)
        self.assertEqual(stock_on_hand(self.conn, "product:cola-330"), 12)

    def test_09_pending_approvals(self):
        append_event(
            self.conn,
            type="approval.requested",
            actor="master",
            payload=approval_payload("A-1"),
        )
        self.assertEqual(len(pending_approvals(self.conn)), 1)
        append_event(
            self.conn,
            type="approval.decided",
            actor="owner",
            payload={"approval_id": "A-1", "decision": "yes", "channel": "web"},
        )
        self.assertEqual(pending_approvals(self.conn), [])

    def test_10_money(self):
        self.assertEqual(line_net_cents(48, 62), 2976)
        self.assertEqual(line_net_cents(10, 174), 1740)
        self.assertEqual(vat_cents(174, 13), 23)
        self.assertEqual(format_eur(123456), "1.234,56 €")
        self.assertEqual(format_eur(-197), "−1,97 €")
        self.assertEqual(format_eur(5), "0,05 €")

    def test_11_catalog_matches_schema_doc(self):
        if not SCHEMA_DOC.exists():
            self.skipTest("schema document not found (not the preparation repo)")
        text = SCHEMA_DOC.read_text(encoding="utf-8")
        for event_type in CATALOG:
            self.assertIn(f"`{event_type}`", text, event_type)
        section = text.split("## 3.", 1)[1].split("## 4.", 1)[0]
        documented = set()
        for line in section.splitlines():
            if line.startswith("| `"):
                first_cell = line.split("|")[1]
                documented.update(re.findall(r"`([^`]+)`", first_cell))
        self.assertTrue(documented)
        self.assertEqual(documented - set(CATALOG), set())


if __name__ == "__main__":
    unittest.main()
