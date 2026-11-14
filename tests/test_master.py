import os
import unittest
from types import SimpleNamespace
from unittest import mock

from xangx.master import agent, telegram
from xangx.master import tools as tool_catalog
from xangx.shared import append_event, connect, init_db, read_events


def new_db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def types_of(conn):
    return [e.type for e in read_events(conn)]


def add_product(conn, pid, name, threshold):
    conn.execute(
        "INSERT INTO products (id, name, vat_rate, sell_price_cents, low_threshold) "
        "VALUES (?, ?, 24, 100, ?)",
        (pid, name, threshold),
    )
    conn.commit()


def stock(conn, pid, qty):
    append_event(
        conn, type="stock.moved", actor="stock",
        payload={"product_id": pid, "qty": qty, "reason": "count"},
    )


def tool_use_response(name, args, block_id="tu-1"):
    block = SimpleNamespace(type="tool_use", id=block_id, name=name, input=args)
    return SimpleNamespace(stop_reason="tool_use", content=[block], stop_details=None)


def text_response(text, stop_reason="end_turn"):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(stop_reason=stop_reason, content=[block], stop_details=None)


def mock_client(responses):
    create = mock.Mock(side_effect=responses)
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return client, create


class MasterTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        self._saved_tools = dict(tool_catalog.TOOLS)
        self.runs = []

        @tool_catalog.tool(
            "fake_send",
            "Send a fake order.",
            {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
            needs_approval=True,
            approval_action="send_order",
        )
        def fake_send(conn, order_id):
            self.runs.append(order_id)
            return {"summary": f"Στάλθηκε η {order_id}"}

        patcher = mock.patch.dict(os.environ, {"XANGX_FAKE_LLM": "0", "ANTHROPIC_API_KEY": "test-not-real"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        tool_catalog.TOOLS.clear()
        tool_catalog.TOOLS.update(self._saved_tools)
        self.conn.close()

    def ask_with(self, responses, text="Στείλε την παραγγελία P-1"):
        client, create = mock_client(responses)
        with mock.patch.object(agent, "_get_client", return_value=client):
            result = agent.handle_owner_message(self.conn, text)
        return result, create


class FakeModeTest(MasterTestCase):
    def test_fake_llm_answers_and_writes_events(self):
        add_product(self.conn, "cola-330", "Coca-Cola 330ml", 6)
        stock(self.conn, "cola-330", 8)
        add_product(self.conn, "chips", "Πατατάκια", 5)
        stock(self.conn, "chips", 2)
        with mock.patch.dict(os.environ, {"XANGX_FAKE_LLM": "1"}):
            result = agent.handle_owner_message(self.conn, "Τι γίνεται σήμερα;")
        self.assertIn(agent.TEST_MODE_NOTE, result["reply"])
        self.assertIn("Πατατάκια", result["reply"])
        types = types_of(self.conn)
        for wanted in ("owner.said", "agent.planned", "agent.tool_ran", "agent.said"):
            self.assertIn(wanted, types)
        said = read_events(self.conn, types=["owner.said"])[0]
        for event in read_events(self.conn, after_id=said.id):
            self.assertEqual(event.caused_by, said.id)
        self.assertEqual(result["event_ids"], [e.id for e in read_events(self.conn, after_id=said.id - 1)])

    def test_missing_key_means_fake_mode(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertTrue(agent.use_fake_llm())
            result = agent.handle_owner_message(self.conn, "γεια")
        self.assertIn(agent.TEST_MODE_NOTE, result["reply"])


class ApprovalTest(MasterTestCase):
    def test_approval_tool_is_not_run_but_requested(self):
        result, create = self.ask_with(
            [
                tool_use_response("fake_send", {"order_id": "P-1"}),
                text_response("Περιμένει έγκριση."),
            ]
        )
        self.assertEqual(self.runs, [])
        requested = read_events(self.conn, types=["approval.requested"])
        self.assertEqual(len(requested), 1)
        payload = requested[0].payload
        self.assertEqual(payload["action"], "send_order")
        self.assertEqual(payload["tool"], "fake_send")
        self.assertEqual(payload["args"], {"order_id": "P-1"})
        self.assertTrue(payload["approval_id"].startswith("A-"))
        self.assertNotIn("agent.tool_ran", types_of(self.conn))
        self.assertEqual(result["reply"], "Περιμένει έγκριση.")
        # the model got a tool_result saying it waits for approval
        second_call_messages = create.call_args_list[1].kwargs["messages"]
        tool_result = second_call_messages[-1]["content"][0]
        self.assertIn("Περιμένει έγκριση", tool_result["content"])

    def test_api_call_uses_model_effort_and_fallbacks(self):
        _, create = self.ask_with([text_response("ok")])
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["max_tokens"], 4096)
        self.assertTrue(all(t["strict"] for t in kwargs["tools"]))

    def _requested_id(self):
        self.ask_with(
            [tool_use_response("fake_send", {"order_id": "P-1"}), text_response("ok")]
        )
        return read_events(self.conn, types=["approval.requested"])[0].payload["approval_id"]

    def test_yes_runs_once_and_second_call_does_not(self):
        approval_id = self._requested_id()
        first = agent.decide_approval(self.conn, approval_id, "yes")
        self.assertTrue(first["ok"])
        self.assertEqual(self.runs, ["P-1"])
        second = agent.decide_approval(self.conn, approval_id, "yes")
        self.assertFalse(second["ok"])
        self.assertEqual(self.runs, ["P-1"])
        self.assertEqual(len(read_events(self.conn, types=["approval.decided"])), 1)
        self.assertEqual(len(read_events(self.conn, types=["agent.tool_ran"])), 1)

    def test_no_does_not_run(self):
        approval_id = self._requested_id()
        result = agent.decide_approval(self.conn, approval_id, "no")
        self.assertTrue(result["ok"])
        self.assertEqual(self.runs, [])

    def test_unknown_approval_and_bad_decision(self):
        self.assertFalse(agent.decide_approval(self.conn, "A-999", "yes")["ok"])
        self.assertFalse(agent.decide_approval(self.conn, "A-999", "maybe")["ok"])


class PauseAndRefusalTest(MasterTestCase):
    def pause(self):
        append_event(self.conn, type="system.paused", actor="owner", payload={})

    def test_paused_runs_no_tool_and_does_not_call_claude(self):
        self.pause()
        client, create = mock_client([])
        with mock.patch.object(agent, "_get_client", return_value=client):
            result = agent.handle_owner_message(self.conn, "κάνε κάτι")
        create.assert_not_called()
        self.assertIn("παύση", result["reply"])
        self.assertNotIn("agent.tool_ran", types_of(self.conn))
        self.assertIn("owner.said", types_of(self.conn))

    def test_yes_while_paused_does_not_run(self):
        approval_id = None
        self.ask_with([tool_use_response("fake_send", {"order_id": "P-2"}), text_response("ok")])
        approval_id = read_events(self.conn, types=["approval.requested"])[0].payload["approval_id"]
        self.pause()
        result = agent.decide_approval(self.conn, approval_id, "yes")
        self.assertFalse(result["ok"])
        self.assertEqual(self.runs, [])
        self.assertEqual(read_events(self.conn, types=["approval.decided"]), [])

    def test_refusal_gives_fixed_answer(self):
        refusal = SimpleNamespace(stop_reason="refusal", content=[], stop_details=None)
        result, _ = self.ask_with([refusal])
        self.assertEqual(result["reply"], "Δεν μπορώ να το κάνω αυτό.")

    def test_api_error_is_reported_not_raised(self):
        client, create = mock_client([RuntimeError("network")])
        with mock.patch.object(agent, "_get_client", return_value=client):
            result = agent.handle_owner_message(self.conn, "γεια")
        self.assertEqual(result["reply"], agent.MSG_API_ERROR)

    def test_failing_tool_returns_is_error(self):
        @tool_catalog.tool("broken", "Always fails.", {"type": "object", "properties": {}})
        def broken(conn):
            raise ValueError("boom")

        _, create = self.ask_with([tool_use_response("broken", {}), text_response("ok")])
        tool_result = create.call_args_list[1].kwargs["messages"][-1]["content"][0]
        self.assertTrue(tool_result["is_error"])
        ran = read_events(self.conn, types=["agent.tool_ran"])[0]
        self.assertFalse(ran.payload["ok"])

    def test_echo_content_drops_blocks_before_last_fallback(self):
        blocks = [
            SimpleNamespace(type="thinking"),
            SimpleNamespace(type="tool_use", id="x"),
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="fallback"),
            SimpleNamespace(type="tool_use", id="y"),
        ]
        kept = agent._echo_content(blocks)
        self.assertEqual([b.type for b in kept], ["text", "fallback", "tool_use"])


class ToolsTest(MasterTestCase):
    def test_tool_definitions_are_strict_with_no_extra_properties(self):
        for definition in tool_catalog.tool_definitions():
            self.assertTrue(definition["strict"])
            self.assertIs(definition["input_schema"]["additionalProperties"], False)
        names = [d["name"] for d in tool_catalog.tool_definitions()]
        self.assertEqual(names, sorted(names))
        for needed in ("get_store_status", "list_recent_events", "notify_owner", "fake_send"):
            self.assertIn(needed, names)

    def test_notify_owner_and_list_recent_events(self):
        result = tool_catalog.TOOLS["notify_owner"].fn(self.conn, text="γεια")
        self.assertEqual(len(result["event_ids"]), 1)
        listed = tool_catalog.TOOLS["list_recent_events"].fn(self.conn, limit=500)
        self.assertEqual(listed["events"][-1]["type"], "notification.sent")
        only = tool_catalog.TOOLS["list_recent_events"].fn(self.conn, types=["sale.recorded"])
        self.assertEqual(only["events"], [])

    def test_store_status_sees_camera_low_and_lists_suppliers(self):
        add_product(self.conn, "cola", "Coca-Cola", 5)
        add_product(self.conn, "water", "Νερό", 5)
        add_product(self.conn, "chips", "Chips", 5)
        stock(self.conn, "cola", 8)
        stock(self.conn, "water", 30)
        stock(self.conn, "chips", 2)
        append_event(
            self.conn, type="stock.low_detected", actor="stock", subject="product:cola",
            payload={"product_id": "cola", "on_hand": 4, "per_day": 3, "days_left": 1, "source": "camera"},
        )
        append_event(
            self.conn, type="stock.low_detected", actor="stock", subject="product:cola",
            payload={"product_id": "cola", "on_hand": 3, "per_day": 3, "days_left": 1, "source": "camera"},
        )
        append_event(
            self.conn, type="stock.low_detected", actor="stock", subject="product:chips",
            payload={"product_id": "chips", "on_hand": 1, "per_day": 3, "days_left": 1, "source": "camera"},
        )
        self.conn.execute("INSERT INTO suppliers (id, name) VALUES ('sup-a', 'Α')")
        self.conn.execute("INSERT INTO suppliers (id, name) VALUES ('sup-b', 'Β')")
        self.conn.execute("INSERT INTO supplier_products (supplier_id, product_id) VALUES ('sup-a', 'cola')")
        self.conn.execute("INSERT INTO supplier_products (supplier_id, product_id) VALUES ('sup-a', 'water')")
        self.conn.execute("INSERT INTO supplier_products (supplier_id, product_id) VALUES ('sup-b', 'chips')")
        self.conn.commit()
        status = tool_catalog.TOOLS["get_store_status"].fn(self.conn)
        low = {p["product_id"]: p["on_hand"] for p in status["low_products"]}
        self.assertEqual(low, {"cola": 3, "chips": 1})
        self.assertEqual(len(status["low_products"]), 2)
        by_id = {s["id"]: s for s in status["suppliers"]}
        self.assertEqual(by_id["sup-a"]["name"], "Α")
        self.assertEqual(by_id["sup-a"]["low_product_ids"], ["cola"])
        self.assertEqual(by_id["sup-b"]["low_product_ids"], ["chips"])

    def test_load_role_tools_ignores_missing_modules(self):
        tool_catalog.load_role_tools()


class MorningCheckTest(MasterTestCase):
    def test_writes_check_morning_done(self):
        add_product(self.conn, "cola-330", "Coca-Cola 330ml", 6)
        stock(self.conn, "cola-330", 8)
        add_product(self.conn, "chips", "Πατατάκια", 5)
        stock(self.conn, "chips", 2)
        self.ask_with([tool_use_response("fake_send", {"order_id": "P-3"}), text_response("ok")])
        event_id = agent.morning_check(self.conn)
        event = [e for e in read_events(self.conn) if e.id == event_id][0]
        self.assertEqual(event.type, "check.morning_done")
        findings = event.payload["findings"]
        self.assertIn({"type": "low_stock", "ref": "chips"}, findings)
        self.assertNotIn({"type": "low_stock", "ref": "cola-330"}, findings)
        self.assertTrue(any(f["type"] == "approval_pending" for f in findings))


class TelegramTest(MasterTestCase):
    OWNER = "111"

    def api(self):
        api = mock.Mock()
        api.call = mock.Mock(return_value={})
        return api

    def test_ignores_other_chats(self):
        api = self.api()
        update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "hi"}}
        telegram.process_update(self.conn, api, update, self.OWNER)
        api.call.assert_not_called()
        self.assertEqual(read_events(self.conn), [])
        callback = {"update_id": 2, "callback_query": {"id": "c", "data": "ap:A-1:yes", "message": {"chat": {"id": 999}}}}
        telegram.process_update(self.conn, api, callback, self.OWNER)
        api.call.assert_not_called()

    def test_owner_message_is_answered(self):
        api = self.api()
        update = {"update_id": 1, "message": {"chat": {"id": 111}, "text": "τι γίνεται"}}
        with mock.patch.dict(os.environ, {"XANGX_FAKE_LLM": "1"}):
            telegram.process_update(self.conn, api, update, self.OWNER)
        method = api.call.call_args.args[0]
        self.assertEqual(method, "sendMessage")
        self.assertIn(agent.TEST_MODE_NOTE, api.call.call_args.kwargs["text"])
        said = read_events(self.conn, types=["owner.said"])[0]
        self.assertEqual(said.payload["channel"], "telegram")

    def test_callback_decides_approval(self):
        self.ask_with([tool_use_response("fake_send", {"order_id": "P-4"}), text_response("ok")])
        approval_id = read_events(self.conn, types=["approval.requested"])[0].payload["approval_id"]
        api = self.api()
        callback = {
            "update_id": 3,
            "callback_query": {"id": "c1", "data": f"ap:{approval_id}:yes", "message": {"chat": {"id": 111}}},
        }
        telegram.process_update(self.conn, api, callback, self.OWNER)
        self.assertEqual(self.runs, ["P-4"])
        self.assertEqual(read_events(self.conn, types=["approval.decided"])[0].payload["channel"], "telegram")

    def test_notifier_sends_buttons_once_and_skips_decided(self):
        self.ask_with([tool_use_response("fake_send", {"order_id": "P-5"}), text_response("ok")])
        api = self.api()
        sent = telegram.notify_once(self.conn, api, self.OWNER)
        self.assertEqual(sent, 1)
        params = api.call.call_args.kwargs
        buttons = params["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["Ναι", "Όχι"])
        api.call.reset_mock()
        self.assertEqual(telegram.notify_once(self.conn, api, self.OWNER), 0)
        api.call.assert_not_called()

    def test_start_without_token_does_nothing(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_CHAT_ID": ""}):
            telegram.start()
            agent.start_background()


if __name__ == "__main__":
    unittest.main()
