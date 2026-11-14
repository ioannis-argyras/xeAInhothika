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

    def test_invoice_matched_without_delivery(self):
        none = Event(1, "x", "s", "invoices", "invoice.matched", None, None, None,
                     {"invoice_id": "i1", "issues": []}, 1)
        self.assertIn("δεν βρέθηκε παραλαβή για ταίριασμα", describe(none))
        self.assertNotIn("ταιριάζει", describe(none))
        found = Event(2, "x", "s", "invoices", "invoice.matched", None, None, None,
                      {"invoice_id": "i1", "issues": [], "delivery_id": "d1"}, 1)
        self.assertIn("ταιριάζει με την παραλαβή", describe(found))

    def test_taken_sentence_and_iban_mask(self):
        names = {"position": {"p": "Ψυγείο 1"}, "product": {"c": "Coca-Cola 330ml"}}
        event = Event(1, "x", "s", "camera", "shelf.items_taken", None, None, None,
                      {"position_id": "p", "product_id": "c", "taken": 5}, 1)
        self.assertEqual(describe(event, names), "Ψυγείο 1: πήραν 5 Coca-Cola 330ml")
        note = Event(2, "x", "s", "accountant", "exception.flagged", None, None, None,
                     {"kind": "iban", "ref": "r",
                      "note": "Νέο GR1601101250000000012300695 στο τιμολόγιο"}, 1)
        self.assertNotIn("GR1601101250000000012300695", describe(note))


class HomeStateTest(unittest.TestCase):
    """Data for the new home screen: day, tasks, team, timeline, shelf focus, approval lines."""

    EXACT = "24 Coca-Cola 330ml × 0,58 €\n36 Patataka alati 90g × 0,35 €"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        self.client = TestClient(server_app.app)
        conn = connect(self.db)
        init_db(conn)
        conn.execute("INSERT INTO products VALUES ('cola-330','Coca-Cola 330ml',NULL,'x',13,100,6)")
        conn.execute("INSERT INTO positions VALUES ('fr1-a','Ψυγείο 1','cola-330','cam-1')")
        conn.commit()
        append_event(conn, type="check.morning_done", actor="master", payload={"findings": []})
        append_event(conn, type="stock.moved", actor="stock",
                     payload={"product_id": "cola-330", "qty": 3, "reason": "count"})
        append_event(conn, type="shelf.items_taken", actor="camera", payload={
            "position_id": "fr1-a", "product_id": "cola-330", "taken": 5,
            "confidence": 0.9, "file_ref": "a", "prev_file_ref": "b"})
        append_event(conn, type="stock.low_detected", actor="stock", payload={
            "product_id": "cola-330", "on_hand": 3, "per_day": 22.7, "days_left": 0.1,
            "source": "t"})
        append_event(conn, type="approval.requested", actor="master", payload={
            "approval_id": "A-1", "action": "send_order", "summary": "Να στείλω;",
            "exact": self.EXACT, "why": "Γιατί", "ref": "P-1"})
        append_event(conn, type="agent.tool_ran", actor="master", payload={
            "tool": "x", "summary": "s", "ok": False, "ms": 1, "result_event_ids": []})
        conn.close()

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_tasks_team_and_timeline(self):
        data = self.client.get("/api/state").json()
        self.assertEqual(data["tasks"], {"done": 2, "with_yes": 0, "errors": 1})
        self.assertEqual([t["key"] for t in data["team"]],
                         ["stock", "orders", "invoices", "accountant"])
        self.assertEqual(data["team"][0]["last"]["type"], "stock.low_detected")
        self.assertTrue(data["team"][0]["active"])
        self.assertIsNone(data["team"][1]["last"])
        self.assertFalse(data["team"][1]["active"])
        first = data["timeline"][0]
        self.assertEqual(first["team"], "master")
        now = server_app._local(datetime.now(timezone.utc))
        self.assertLessEqual(abs(first["minute"] - (now.hour * 60 + now.minute)), 1)
        self.assertEqual([e["id"] for e in data["timeline"]], [1, 4, 5])
        day = data["day"]
        self.assertEqual(day["date"], now.strftime("%d/%m/%Y"))
        self.assertEqual(day["now"], now.strftime("%H:%M"))
        self.assertIn(day["weekday"], server_app.WEEKDAYS)
        self.assertEqual(day["morning_done_at"], day["now"])

    def test_shelf_focus(self):
        focus = self.client.get("/api/state").json()["shelf_focus"]
        self.assertEqual(focus["product_name"], "Coca-Cola 330ml")
        self.assertEqual(focus["position_name"], "Ψυγείο 1")
        self.assertEqual((focus["taken"], focus["left"]), (5, 3))
        self.assertEqual(focus["before"], focus["left"] + focus["taken"])

    def test_approval_lines(self):
        approval = self.client.get("/api/state").json()["pending_approvals"][0]
        self.assertEqual(approval["lines"], [
            {"qty": 24, "text": "Coca-Cola 330ml"},
            {"qty": 36, "text": "Patataka alati 90g"},
        ])
        self.assertEqual(server_app.exact_lines("24 Coca-Cola 330ml × 0,58 €"),
                         [{"qty": 24, "text": "Coca-Cola 330ml"}])
        self.assertEqual(server_app.exact_lines("Email προς x, θέμα y:\nγεια σου"), [])
        self.assertEqual(server_app.exact_lines(None), [])

    def test_until_id_ignores_later_events(self):
        data = self.client.get("/api/state?until_id=2").json()
        self.assertEqual(data["tasks"], {"done": 1, "with_yes": 0, "errors": 0})
        self.assertEqual(data["pending_approvals"], [])
        self.assertIsNone(data["shelf_focus"])
        self.assertEqual([e["id"] for e in data["timeline"]], [1])
        self.assertEqual(data["team"][0]["last"]["id"], 2)

    def test_empty_db_has_the_new_keys(self):
        os.remove(self.db)
        data = self.client.get("/api/state").json()
        self.assertEqual(data["tasks"], {"done": 0, "with_yes": 0, "errors": 0})
        self.assertEqual(data["timeline"], [])
        self.assertIsNone(data["shelf_focus"])
        self.assertIsNone(data["day"]["morning_done_at"])
        self.assertTrue(all(t["last"] is None and not t["active"] for t in data["team"]))


if __name__ == "__main__":
    unittest.main()
