import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xangx.demo.seed import seed_all
from xangx.server import access
from xangx.server import app as server_app
from xangx.server import mcp
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events

TOKEN = secrets.token_urlsafe(32)  # made fresh for every test run, never written anywhere
ORIGIN_EVIL = "https://evil.example"


class McpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory()
        cls.seed_db = os.path.join(cls.seed_dir.name, "seed.sqlite")
        conn = connect(cls.seed_db)
        init_db(conn)
        seed_all(conn)
        # The shelf camera saw Coca-Cola run low today. (Written directly: the stock worker would
        # first walk the whole seeded history, which takes about a minute here.)
        append_event(conn, type="stock.low_detected", actor="stock",
                     payload={"product_id": "cola-330", "on_hand": 2, "per_day": 12,
                              "days_left": 0.2, "source": "camera"})
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.seed_dir.cleanup()
        except OSError:
            pass

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        src, dst = connect(self.seed_db), connect(self.db)
        src.backup(dst)
        src.close()
        dst.close()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SMTP_") and k not in ("XANGX_DEMO_SUPPLIER_EMAIL", "XANGX_ACCESS_CODE")}
        env.update({
            "XANGX_DB": self.db,
            "XANGX_FILES": os.path.join(self.dir.name, "files"),
            "XANGX_DATA_DIR": os.path.join(self.dir.name, "data"),
            "XANGX_FAKE_LLM": "1",
            "XANGX_MCP_TOKEN": TOKEN,
        })
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        access.reset_attempts()
        self.conn = connect(self.db)
        self.client = TestClient(server_app.app, follow_redirects=False)
        self.ibans = [i for r in self.conn.execute("SELECT known_ibans FROM suppliers")
                      for i in json.loads(r["known_ibans"])]
        self.seen = []  # every response text of the test, for the IBAN check

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        access.reset_attempts()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    # --- helpers ---
    def post(self, body, token=TOKEN, headers=None):
        h = {"Authorization": f"Bearer {token}"} if token is not None else {}
        h.update(headers or {})
        r = self.client.post("/mcp", content=json.dumps(body), headers=h)
        self.seen.append(r.text)
        return r

    def rpc(self, method, params=None, id_=1, **kw):
        body = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            body["params"] = params
        return self.post(body, **kw)

    def call(self, name, arguments=None):
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        r = self.rpc("tools/call", params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def events(self, *types):
        return read_events(self.conn, types=list(types))

    def count_events(self):
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def outbox_files(self):
        out = Path(self.dir.name) / "data" / "outbox"
        return list(out.glob("*")) if out.exists() else []

    # --- door and token ---
    def test_closed_without_token_setting(self):
        for value in (None, "", "short-token-123"):
            with mock.patch.dict(os.environ, {"XANGX_MCP_TOKEN": value or ""}):
                r = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
                self.assertEqual(r.status_code, 404, value)
                self.assertEqual(self.client.get("/mcp").status_code, 404)

    def test_token_needed(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        r = self.client.post("/mcp", json=ping)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"], "Bearer")
        self.assertNotIn("result", r.text)
        r = self.post(ping, token="wrong-" + TOKEN)
        self.assertEqual(r.status_code, 401)
        r = self.post(ping)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"], {})

    def test_browser_origin_is_refused(self):
        r = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Origin": ORIGIN_EVIL})
        self.assertEqual(r.status_code, 403)
        r = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Origin": "null"})
        self.assertEqual(r.status_code, 200)

    def test_six_wrong_tokens_lock(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for _ in range(5):
            self.assertEqual(self.post(ping, token="nope").status_code, 401)
        self.assertEqual(self.post(ping, token="nope").status_code, 429)
        self.assertEqual(self.post(ping).status_code, 429)  # even the right one, while locked
        # the login page of the team code is not locked by this
        self.assertEqual(self.client.get("/login").status_code, 303)

    def test_body_limit_get_and_batch(self):
        big = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": "a" * 70000}}
        self.assertEqual(self.post(big).status_code, 413)
        self.assertEqual(self.client.get("/mcp").status_code, 405)
        r = self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(r.json()["error"]["code"], -32600)
        self.assertEqual(self.rpc("nope").json()["error"]["code"], -32601)

    def test_access_code_does_not_cover_other_paths(self):
        os.environ["XANGX_ACCESS_CODE"] = "team-code-1234"
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code, 200)
        self.assertEqual(self.client.get("/mcpx").status_code, 401)
        self.assertEqual(self.client.post("/mcpx", json={}).status_code, 401)
        self.assertEqual(self.client.get("/mcp/../api/state").status_code, 401)
        self.assertEqual(self.client.get("/mcp/x").status_code, 401)
        self.assertEqual(self.client.get("/api/state").status_code, 401)

    # --- protocol ---
    def test_initialize_and_initialized(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}})
        res = r.json()["result"]
        self.assertEqual(res["protocolVersion"], "2025-03-26")
        self.assertEqual(res["serverInfo"]["name"], "xangxothaika")
        self.assertEqual(res["capabilities"], {"tools": {"listChanged": False}})
        self.assertTrue(res["instructions"])
        self.assertTrue(r.headers.get("mcp-session-id"))
        r = self.rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r.json()["result"]["protocolVersion"], "2025-06-18")
        r = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_tools_list(self):
        tools = self.rpc("tools/list").json()["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        self.assertEqual([t["name"] for t in tools], sorted(by_name))
        self.assertIn("get_store_status", by_name)
        self.assertNotIn("notify_owner", by_name)
        self.assertIn("Χρειάζεται έγκριση", by_name["send_order"]["description"])
        self.assertNotIn("Χρειάζεται έγκριση", by_name["get_store_status"]["description"])
        self.assertEqual(by_name["send_order"]["inputSchema"]["required"], ["order_id"])

    def test_tools_have_greek_titles(self):
        tools = self.rpc("tools/list").json()["result"]["tools"]
        for t in tools:
            self.assertEqual(t["title"], mcp.TITLES_EL[t["name"]])
        self.assertEqual(len(tools), len(mcp.TITLES_EL))

    def test_marker_summary_is_greek_title(self):
        self.call("get_store_status", {})
        marker = [e for e in self.events("agent.planned") if e.payload.get("via") == "mcp"]
        self.assertEqual(len(marker), 1)
        self.assertEqual(marker[0].payload["steps"][0]["summary"], "Κατάσταση του μαγαζιού")

    # --- tools/call ---
    def test_unknown_and_excluded_tool(self):
        for name in ("no_such_tool", "notify_owner"):
            before = self.count_events()
            r = self.call(name, {"text": "x"} if name == "notify_owner" else {})
            self.assertEqual(r["error"]["code"], -32602)
            self.assertEqual(self.count_events(), before)
        self.assertEqual(self.events("notification.sent"), [])

    def test_get_store_status_runs_and_is_logged(self):
        r = self.call("get_store_status", {})["result"]
        self.assertFalse(r["isError"])
        self.assertIn("pending_approvals", r["structuredContent"])
        self.assertIn("Κατάσταση", r["content"][0]["text"])
        ran = self.events("agent.tool_ran")
        self.assertEqual([e.payload["tool"] for e in ran], ["get_store_status"])
        marker = [e for e in self.events("agent.planned") if e.payload.get("via") == "mcp"]
        self.assertEqual(len(marker), 1)
        self.assertEqual(ran[0].caused_by, marker[0].id)

    def test_list_open_invoices(self):
        r = self.call("list_open_invoices", {})["result"]
        self.assertFalse(r["isError"])
        self.assertIn("invoices", r["structuredContent"])

    def test_send_order_only_makes_the_owner_card(self):
        proposed = self.call("propose_order", {"supplier_id": "sup-ymittou"})["result"]
        order_id = proposed["structuredContent"]["order_id"]
        before_sent = len(self.events("order.sent"))
        r1 = self.call("send_order", {"order_id": order_id})["result"]
        self.assertFalse(r1["isError"])
        self.assertEqual(r1["structuredContent"]["status"], "waiting_for_owner")
        self.assertIn("Περιμένει έγκριση του ιδιοκτήτη.", r1["content"][0]["text"])
        approval_id = r1["structuredContent"]["approval_id"]
        requested = self.events("approval.requested")
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0].payload["approval_id"], approval_id)
        self.assertEqual(requested[0].payload["tool"], "send_order")
        self.assertEqual(len(self.events("order.sent")), before_sent)
        self.assertEqual(self.outbox_files(), [])
        # same call again: the same card, not a second one
        r2 = self.call("send_order", {"order_id": order_id})["result"]
        self.assertEqual(r2["structuredContent"]["approval_id"], approval_id)
        self.assertEqual(len(self.events("approval.requested")), 1)
        self.assertEqual(self.events("approval.decided"), [])
        self.assertEqual(self.outbox_files(), [])

    def test_bad_arguments_change_nothing(self):
        cases = [
            ("send_order", {}),                                  # missing field
            ("send_order", {"order_id": "P-1", "extra": 1}),     # extra field
            ("send_order", {"order_id": 5}),                     # wrong type
            ("send_order", {"order_id": "x" * 2001}),            # too long
            ("daily_report", {"date": ["2026-11-14"]}),          # wrong type
            ("list_recent_events", {"limit": "5"}),              # integer as string
            ("list_recent_events", {"limit": True}),             # bool is not an integer
            ("get_store_status", {"x": 1}),                      # no fields allowed
        ]
        for name, args in cases:
            before = self.count_events()
            r = self.call(name, args)["result"]
            self.assertTrue(r["isError"], (name, args))
            self.assertTrue(r["content"][0]["text"])
            self.assertEqual(self.count_events(), before, (name, args))

    def test_paused_store_runs_nothing(self):
        append_event(self.conn, type="system.paused", actor="owner", payload={})
        for name, args in (("get_store_status", {}), ("send_order", {"order_id": "P-0319"})):
            before = self.count_events()
            r = self.call(name, args)["result"]
            self.assertTrue(r["isError"])
            self.assertIn("σε παύση", r["content"][0]["text"])
            self.assertEqual(self.count_events(), before)

    def test_no_full_iban_in_any_answer(self):
        self.assertTrue(self.ibans)
        self.call("get_store_status", {})
        self.call("list_open_invoices", {})
        self.call("list_recent_events", {"limit": 50})
        self.call("propose_order", {"supplier_id": "sup-ymittou"})
        self.call("daily_report", {"date": "2026-11-14"})
        self.rpc("tools/list")
        blob = "\n".join(self.seen).replace(" ", "")
        for iban in self.ibans:
            self.assertNotIn(iban.replace(" ", ""), blob)


if __name__ == "__main__":
    unittest.main()
