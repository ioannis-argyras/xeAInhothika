import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from xangx.screen.describe import describe
from xangx.server import app as server_app
from xangx.shared.catalog import CATALOG
from xangx.shared.db import connect, init_db
from xangx.shared.events import Event, append_event


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        self.client = TestClient(server_app.app)

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_state_on_empty_db(self):
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in ("pending_approvals", "shelves", "stock", "today", "recent",
                    "paused", "last_id"):
            self.assertIn(key, data)
        self.assertEqual(data["today"], {"sales_cents": 0, "sales_count": 0})
        self.assertFalse(data["paused"])
        self.assertEqual(data["last_id"], 0)

    def test_index_and_static(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_pause_and_resume(self):
        self.assertTrue(self.client.post("/api/pause").json()["ok"])
        self.assertTrue(self.client.get("/api/state").json()["paused"])
        self.client.post("/api/resume")
        self.assertFalse(self.client.get("/api/state").json()["paused"])

    def test_chat_uses_master(self):
        fake = types.ModuleType("xangx.master.agent")
        fake.handle_owner_message = mock.Mock(
            return_value={"reply": "Γεια σου", "event_ids": [1]})
        fake.decide_approval = mock.Mock(
            return_value={"ok": True, "message": "ok", "event_ids": []})
        with mock.patch.dict(sys.modules, {"xangx.master.agent": fake}):
            r = self.client.post("/api/chat", json={"text": "γεια"})
            self.assertEqual(r.json()["reply"], "Γεια σου")
            self.assertEqual(fake.handle_owner_message.call_args[0][1:], ("γεια", "web"))
            r = self.client.post("/api/approvals/A-1", json={"decision": "yes"})
            self.assertTrue(r.json()["ok"])
            r = self.client.post("/api/approvals/A-1", json={"decision": "maybe"})
            self.assertEqual(r.status_code, 400)

    def test_screens_always_revalidate(self):
        self.assertEqual(self.client.get("/static/app.js").headers["cache-control"], "no-cache")

    def test_role_routes_are_included(self):
        from fastapi import APIRouter, FastAPI

        fake = types.ModuleType("xangx.stock.routes")
        fake.router = APIRouter()
        fake.router.get("/stock/ping")(lambda: {"ok": True})
        target = FastAPI()
        with mock.patch.dict(sys.modules, {"xangx.stock.routes": fake}):
            self.assertIn("xangx.stock.routes", server_app.include_role_routes(target))
        self.assertTrue(TestClient(target).get("/stock/ping").json()["ok"])

    def test_chat_without_master_is_503(self):
        with mock.patch.dict(sys.modules, {"xangx.master.agent": None}):
            r = self.client.post("/api/chat", json={"text": "γεια"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("συνεργάτης", r.json()["detail"])

    def test_state_with_data(self):
        conn = connect(self.db)
        init_db(conn)
        conn.execute("INSERT INTO products VALUES ('cola-330','Coca-Cola 330ml',NULL,'x',13,100,6)")
        conn.execute("INSERT INTO positions VALUES ('fr1-a','Ψυγείο 1','cola-330','cam-1')")
        conn.commit()
        append_event(conn, type="stock.moved", actor="stock",
                     payload={"product_id": "cola-330", "qty": 5, "reason": "count"})
        append_event(conn, type="shelf.items_taken", actor="camera", payload={
            "position_id": "fr1-a", "product_id": "cola-330", "taken": 5,
            "confidence": 0.9, "file_ref": "a", "prev_file_ref": "b"})
        append_event(conn, type="sale.recorded", actor="pos-sim", payload={
            "receipt_id": "R1", "lines": [], "total_cents": 250, "payment": "cash"})
        conn.close()
        data = self.client.get("/api/state").json()
        self.assertEqual(data["stock"][0]["product_id"], "cola-330")
        self.assertEqual(data["shelves"][0]["position_name"], "Ψυγείο 1")
        self.assertEqual(data["today"]["sales_count"], 1)
        self.assertEqual(data["today"]["sales_cents"], 250)
        # The till sale counts in "today" but is not listed in the feed.
        self.assertEqual([e["type"] for e in data["recent"]],
                         ["shelf.items_taken", "stock.moved"])
        self.assertIn("Ψυγείο 1: πήραν 5 Coca-Cola 330ml", data["recent"][0]["text"])
        self.assertEqual(self.client.get("/api/state?until_id=1").json()["last_id"], 1)

    def test_new_events_for_stream(self):
        conn = connect(self.db)
        init_db(conn)
        append_event(conn, type="system.paused", actor="owner", payload={})
        conn.close()
        items = server_app.new_events(0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "system.paused")
        self.assertEqual(server_app.new_events(1), [])

    def test_athens_day_start(self):
        winter = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(server_app.athens_day_start_utc(winter), "2026-11-13T22:00:00.000Z")
        summer = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(server_app.athens_day_start_utc(summer), "2026-06-30T21:00:00.000Z")


class DescribeTest(unittest.TestCase):
    def test_every_catalog_type(self):
        for type_, spec in CATALOG.items():
            payload = {name: 1 for name in spec.required}
            event = Event(1, "2026-11-14T10:00:00.000Z", "store-1", "system", type_,
                          None, None, None, payload, 1)
            text = describe(event)
            self.assertIsInstance(text, str)
            self.assertTrue(text)

    def test_unknown_type_gives_type(self):
        event = Event(1, "x", "s", "system", "foo.bar", None, None, None, {}, 1)
        self.assertEqual(describe(event), "foo.bar")

    def test_taken_sentence_and_iban_mask(self):
        names = {"position": {"p": "Ψυγείο 1"}, "product": {"c": "Coca-Cola 330ml"}}
        event = Event(1, "x", "s", "camera", "shelf.items_taken", None, None, None,
                      {"position_id": "p", "product_id": "c", "taken": 5}, 1)
        self.assertEqual(describe(event, names), "Ψυγείο 1: πήραν 5 Coca-Cola 330ml")
        note = Event(2, "x", "s", "accountant", "exception.flagged", None, None, None,
                     {"kind": "iban", "ref": "r",
                      "note": "Νέο GR1601101250000000012300695 στο τιμολόγιο"}, 1)
        self.assertNotIn("GR1601101250000000012300695", describe(note))


if __name__ == "__main__":
    unittest.main()
