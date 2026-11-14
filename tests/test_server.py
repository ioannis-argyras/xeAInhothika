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
from xangx.shared.events import Event, append_event, read_events


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

    def test_invoice_lines_use_paper_number_and_mydata_result(self):
        names = {"invoice": {"i1": "Α-2271"}}

        def ev(type_, payload):
            return Event(1, "x", "s", "invoices", type_, None, None, None, payload, 1)

        read = ev("invoice.read", {"invoice_id": "i1", "total_cents": 1000})
        self.assertIn("Α-2271", describe(read, names))
        own = ev("invoice.read", {"invoice_id": "i9", "series": "Β", "number": "7",
                                  "total_cents": 1000})
        self.assertIn("Β-7", describe(own))
        self.assertIn("i1", describe(read))
        same = ev("invoice.mydata_checked", {"invoice_id": "i1", "found": True,
                                             "totals_match": True})
        self.assertEqual(describe(same, names),
                         "myDATA: βρέθηκε το τιμολόγιο Α-2271, ίδιο σύνολο")
        diff = ev("invoice.mydata_checked", {"invoice_id": "i1", "found": True,
                                             "totals_match": False})
        self.assertIn("ΔΙΑΦΟΡΑ στο σύνολο", describe(diff, names))
        err = ev("invoice.mydata_checked", {"invoice_id": "i1", "found": False,
                                            "totals_match": False, "error": "timeout"})
        self.assertEqual(describe(err, names),
                         "Το myDATA δεν απάντησε για το τιμολόγιο Α-2271, θα ξαναδοκιμάσω")
        missing = ev("invoice.mydata_checked", {"invoice_id": "i1", "found": False,
                                                "totals_match": False})
        self.assertEqual(describe(missing, names), "myDATA: δεν βρέθηκε το τιμολόγιο Α-2271")
        two = ev("invoice.matched", {"invoice_id": "i1", "delivery_id": "d1", "issues": [
            {"note": "Coca-Cola: τιμολογήθηκαν 10, ήρθαν 8"}, {"note": "άλλο"}]})
        self.assertEqual(describe(two, names),
                         "Το τιμολόγιο Α-2271: Coca-Cola: τιμολογήθηκαν 10, ήρθαν 8 και άλλες 1")
        one = ev("invoice.matched", {"invoice_id": "i1", "delivery_id": "d1",
                                     "issues": [{"note": "κάτι"}]})
        self.assertEqual(describe(one, names), "Το τιμολόγιο Α-2271: κάτι")
        nonote = ev("invoice.matched", {"invoice_id": "i1", "delivery_id": "d1",
                                        "issues": [{"kind": "x"}]})
        self.assertIn("έχει 1 αποκλίσεις", describe(nonote, names))

    def test_load_names_reads_paper_numbers(self):
        from xangx.screen.describe import load_names
        conn = connect(":memory:")
        init_db(conn)
        append_event(conn, type="invoice.read", actor="invoices", payload={
            "invoice_id": "i1", "supplier_afm": "1", "series": "Α", "number": "2271",
            "date": "2026-11-14", "lines": [], "net_cents": 0, "vat_by_rate": {},
            "total_cents": 0, "checks": {}, "confidence": "high"})
        self.assertEqual(load_names(conn)["invoice"], {"i1": "Α-2271"})
        conn.close()

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


class AutoRoundsTest(unittest.TestCase):
    """The partner's own start (clock) and another agent's MCP call show on the screens."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        self.client = TestClient(server_app.app)
        self.conn = connect(self.db)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def _woke(self):
        woke, _ = append_event(self.conn, type="agent.woke", actor="master", payload={
            "reason": "Coca-Cola σε χαμηλό", "trigger_ids": [1]})
        append_event(self.conn, type="agent.tool_ran", actor="master", caused_by=woke.id, payload={
            "tool": "x", "summary": "Έλεγξα το απόθεμα", "ok": True, "ms": 1, "result_event_ids": []})
        append_event(self.conn, type="agent.said", actor="master", caused_by=woke.id, payload={
            "text": "Δες την πρόταση παραγγελίας.", "channel": "auto"})
        return woke

    def test_woke_is_a_task_in_timeline_and_count(self):
        woke = self._woke()
        data = self.client.get("/api/state").json()
        self.assertEqual(data["tasks"]["done"], 1)
        self.assertEqual([t["id"] for t in data["timeline"]], [woke.id])
        self.assertEqual(data["timeline"][0]["team"], "master")
        self.assertIn("ξεκίνησε μόνος του", data["timeline"][0]["text"])

    def test_planned_shows_only_with_via_mcp(self):
        append_event(self.conn, type="agent.planned", actor="master", payload={
            "steps": [{"tool": "get_store_status", "summary": "x"}]})
        data = self.client.get("/api/state").json()
        self.assertEqual(data["tasks"]["done"], 0)
        self.assertEqual(data["timeline"], [])
        self.assertEqual(data["rounds"], [])
        mcp, _ = append_event(self.conn, type="agent.planned", actor="master", payload={
            "steps": [{"tool": "get_store_status", "summary": "Δείχνει την κατάσταση"}], "via": "mcp"})
        data = self.client.get("/api/state").json()
        self.assertEqual(data["tasks"]["done"], 1)
        self.assertEqual([(t["id"], t["team"]) for t in data["timeline"]], [(mcp.id, "agent")])
        tasks = self.client.get("/api/screen/tasks").json()
        self.assertEqual(tasks["totals"]["done"], 1)
        self.assertEqual([g["key"] for g in tasks["groups"]], ["agent"])
        self.assertEqual(tasks["groups"][0]["name"], "Άλλος agent")
        replay = self.client.get("/api/screen/replay").json()
        self.assertEqual([(e["id"], e["team"]) for e in replay["events"]], [(mcp.id, "agent")])

    def test_rounds_for_the_conversation(self):
        woke = self._woke()
        mcp, _ = append_event(self.conn, type="agent.planned", actor="master", payload={
            "steps": [{"tool": "get_store_status", "summary": "Δείχνει την κατάσταση"}], "via": "mcp"})
        rounds = self.client.get("/api/state").json()["rounds"]
        self.assertEqual([(r["id"], r["kind"]) for r in rounds], [(woke.id, "clock"), (mcp.id, "mcp")])
        self.assertEqual(rounds[0]["label"], "Μόνος του · Coca-Cola σε χαμηλό")
        self.assertEqual(rounds[0]["text"], "Δες την πρόταση παραγγελίας.")
        self.assertEqual([s["text"] for s in rounds[0]["steps"]], ["Έλεγξα το απόθεμα"])
        self.assertEqual(rounds[1]["label"], "Άλλος agent μέσω MCP")
        self.assertEqual(rounds[1]["text"], "Ζήτησε: Δείχνει την κατάσταση")


class LoginPageTest(unittest.TestCase):
    """The login page is self-contained and its font files are the only static files open before login."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"XANGX_ACCESS_CODE": "abc"})
        self.env.start()
        self.client = TestClient(server_app.app)

    def tearDown(self):
        self.env.stop()

    def test_font_files_are_open_without_login(self):
        for path in ("/static/vendor/ubuntu-sans/ubuntu-sans-greek.woff2",
                     "/static/vendor/phosphor/Phosphor.woff2"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("woff2", r.headers["content-type"])

    def test_everything_else_still_needs_the_code(self):
        for path in ("/static/app.js", "/static/vendor/README.md", "/api/state",
                     "/static/vendor/../app.js"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_login_page_content(self):
        page = self.client.get("/login").text
        self.assertIn("Κωδικός ομάδας", page)
        self.assertIn("Τον κωδικό τον δίνει ο ιδιοκτήτης.", page)
        self.assertNotIn("6 ψηφία", page)
        self.assertNotIn("http", page.replace("http-equiv", ""))
        self.assertIn('autocomplete="current-password"', page)
        self.assertIn('type="password"', page)


class ScreenPagesAccessTest(unittest.TestCase):
    """The tasks, invoices, shelf, time machine and director routes sit behind the access code."""
    PATHS = ("/api/screen/tasks", "/api/screen/invoices", "/api/screen/shelves",
             "/api/screen/replay", "/demo/skinothetis")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"),
            "XANGX_ACCESS_CODE": "test-1234", "XANGX_DATA_DIR": self.dir.name})
        self.env.start()
        self.client = TestClient(server_app.app, follow_redirects=False)

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_blocked_without_login(self):
        for path in self.PATHS:
            r = self.client.get(path, headers={"accept": "text/html"})
            self.assertIn(r.status_code, (302, 303, 401), path)

    def test_served_after_login(self):
        self.assertEqual(self.client.post("/login", data={"code": "test-1234"}).status_code, 303)
        for path in self.PATHS:
            self.assertEqual(self.client.get(path).status_code, 200, path)


class PaymentCardTest(unittest.TestCase):
    """The "Did you pay? How?" card: shown for payment.asked, closed by one answer."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        self.client = TestClient(server_app.app)
        self.conn = connect(self.db)
        init_db(self.conn)
        self.conn.execute("INSERT INTO suppliers (id, name) VALUES ('sup-a', 'Αρτοποιία')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def ask(self, invoice_id="B-512", cents=5170):
        append_event(self.conn, type="payment.asked", actor="accountant", payload={
            "invoice_id": invoice_id, "supplier_id": "sup-a", "amount_cents": cents,
            "invoice_date": "2026-11-14"})

    def cards(self):
        return self.client.get("/api/state").json()["pending_payments"]

    def recorded(self):
        return [e for e in read_events(self.conn) if e.type == "payment.recorded"]

    def test_no_card_without_a_question(self):
        self.assertEqual(self.cards(), [])

    def test_card_for_asked_invoice(self):
        self.ask()
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["title"], "Πλήρωσες το τιμολόγιο B-512;")
        self.assertEqual(cards[0]["text"], "Αρτοποιία, 51,70 €, τοις μετρητοίς")

    def test_answer_writes_one_event_and_closes_the_card(self):
        self.ask()
        for _ in range(2):  # a double click
            r = self.client.post("/api/payments/B-512", json={"method": "card"})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
        recorded = self.recorded()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0].payload, {"invoice_id": "B-512", "method": "card", "amount_cents": 5170})
        self.assertEqual(self.cards(), [])
        feed = self.client.get("/api/state").json()["recent"][0]["text"]
        self.assertEqual(feed, "Πληρώθηκε το B-512 με κάρτα")

    def test_not_yet_hides_it_today_but_not_tomorrow(self):
        from xangx.shared import payments
        self.ask()
        self.client.post("/api/payments/B-512", json={"method": "not_yet"})
        self.assertEqual(self.cards(), [])
        # The same events seen from tomorrow morning: the question comes back.
        self.assertEqual(len(payments.open_payments(self.conn, "2999-01-01T00:00:00.000Z", "2999-01-01")), 1)

    def test_due_reminder_gives_a_card_only_from_its_day(self):
        for iid, due in (("A-1", "2026-11-10"), ("A-2", "2999-01-01")):
            append_event(self.conn, type="payment.reminder_set", actor="accountant", payload={
                "supplier_id": "sup-a", "invoice_id": iid, "due_on": due, "amount_cents": 15929})
        cards = self.cards()
        self.assertEqual([c["invoice_id"] for c in cards], ["A-1"])
        self.assertEqual(cards[0]["text"], "Αρτοποιία, 159,29 €, λήγει 10/11/2026")

    def test_bad_input(self):
        self.ask()
        self.assertEqual(self.client.post("/api/payments/B-512", json={"method": "gold"}).status_code, 400)
        self.assertEqual(self.client.post("/api/payments/X-9", json={"method": "cash"}).status_code, 404)
        self.assertEqual(self.recorded(), [])

    def test_describe_texts(self):
        def ev(type_, payload):
            return Event(1, "2026-11-14T10:00:00.000Z", "store-1", "system", type_, None, None, None, payload, 1)
        names = {"invoice": {"B-512": "Β-512", "G-318": "Γ-318"}, "product": {"milk": "Γάλα 1L"}}
        self.assertEqual(
            describe(ev("payment.asked", {"invoice_id": "B-512", "supplier_id": "s", "amount_cents": 5170,
                                          "invoice_date": "x"}), names),
            "Ρωτώ αν πληρώθηκε το Β-512 (51,70 €, τοις μετρητοίς)")
        self.assertEqual(
            describe(ev("payment.recorded", {"invoice_id": "B-512", "method": "cash", "amount_cents": 1}), names),
            "Πληρώθηκε το Β-512 με μετρητά")
        self.assertEqual(
            describe(ev("payment.recorded", {"invoice_id": "B-512", "method": "not_yet", "amount_cents": 1}), names),
            "Το Β-512 δεν πληρώθηκε ακόμα, θα ξαναρωτήσω αύριο")
        self.assertEqual(
            describe(ev("stock.moved", {"product_id": "milk", "qty": 12, "reason": "invoice",
                                        "invoice_id": "G-318"}), names),
            "Μπήκαν στην αποθήκη 12 Γάλα 1L από το τιμολόγιο Γ-318")


if __name__ == "__main__":
    unittest.main()
