"""The clock: morning check, automatic rounds, pause, approvals, the off switch. No real waiting."""

import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xangx.demo import director
from xangx.master import agent, autorun
from xangx.master import tools as tool_catalog
from xangx.shared import append_event, connect, init_db, read_events
from xangx.stock import worker as stock_worker


def new_db():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def types_of(conn):
    return [e.type for e in read_events(conn)]


def low(conn, product="cola-330", on_hand=3):
    event, _ = append_event(
        conn, type="stock.low_detected", actor="stock",
        payload={"product_id": product, "on_hand": on_hand, "per_day": 10, "days_left": 0.3, "source": "camera"},
    )
    return event.id


def matched(conn, invoice_id, issues):
    event, _ = append_event(
        conn, type="invoice.matched", actor="invoices",
        payload={"invoice_id": invoice_id, "delivery_id": "D-1", "issues": issues},
    )
    return event.id


class MorningTest(unittest.TestCase):
    def setUp(self):
        self.conn = new_db()
        # Greek midnight of today, as a UTC moment: +5 h is 05:00 in Greece, +8 h is 08:00.
        day_start = datetime.strptime(tool_catalog.greek_day_start_utc()[:19], "%Y-%m-%dT%H:%M:%S")
        self.early = day_start.replace(tzinfo=timezone.utc) + timedelta(hours=5)
        self.late = self.early + timedelta(hours=3)

    def test_not_before_seven_then_once_then_not_again(self):
        clock = autorun.Clock()
        self.assertIsNone(clock.morning(self.conn, self.early))
        self.assertNotIn("check.morning_done", types_of(self.conn))
        self.assertIsNotNone(clock.morning(self.conn, self.late))
        self.assertEqual(types_of(self.conn).count("check.morning_done"), 1)
        self.assertIsNone(clock.morning(self.conn, self.late))
        self.assertEqual(types_of(self.conn).count("check.morning_done"), 1)

    def test_start_up_after_seven_catches_up_once(self):
        # a different clock object, as after a restart: the event of today is what counts
        self.assertIsNotNone(autorun.Clock().morning(self.conn, self.late))
        self.assertIsNone(autorun.Clock().morning(self.conn, self.late))

    def test_poll_is_every_30_seconds(self):
        clock = autorun.Clock()
        self.assertIsNone(clock.morning(self.conn, self.early, now=0))
        with mock.patch.object(agent, "morning_check", return_value=1) as check:
            self.assertIsNone(clock.morning(self.conn, self.late, now=10))
            check.assert_not_called()
            self.assertEqual(clock.morning(self.conn, self.late, now=31), 1)


class GatheringTest(unittest.TestCase):
    """The rounds, with the partner itself replaced by a recorder."""

    def setUp(self):
        self.conn = new_db()
        self.clock = autorun.Clock()
        patcher = mock.patch.object(agent, "handle_trigger", return_value={"reply": "ok", "event_ids": []})
        self.round = patcher.start()
        self.addCleanup(patcher.stop)

    def test_three_triggers_inside_ten_seconds_make_one_round(self):
        ids = [low(self.conn, "cola-330"), low(self.conn, "chips"), matched(self.conn, "T-1", ["price"])]
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 10)
        self.round.assert_not_called()  # 45 seconds of quiet have not passed
        self.clock.tick(self.conn, 46)
        self.assertEqual(self.round.call_count, 1)
        conn, reason, trigger_ids = self.round.call_args.args
        self.assertEqual(trigger_ids, ids)
        self.assertIn("σε χαμηλό", reason)
        self.clock.tick(self.conn, 60)
        self.assertEqual(self.round.call_count, 1)

    def test_a_new_trigger_restarts_the_quiet_time(self):
        low(self.conn, "cola-330")
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 40)
        low(self.conn, "chips")
        self.clock.tick(self.conn, 41)
        self.clock.tick(self.conn, 70)  # 29 s after the second one
        self.round.assert_not_called()
        self.clock.tick(self.conn, 90)
        self.assertEqual(self.round.call_count, 1)
        self.assertEqual(len(self.round.call_args.args[2]), 2)

    def test_matched_without_issues_wakes_nobody(self):
        matched(self.conn, "T-1", [])
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 100)
        self.round.assert_not_called()

    def test_other_events_wake_nobody(self):
        append_event(self.conn, type="sale.recorded", actor="pos-sim",
                     payload={"receipt_id": "R-1", "lines": [], "total_cents": 100, "payment": "cash"})
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 100)
        self.round.assert_not_called()

    def test_second_trigger_one_minute_after_the_round_waits_for_three_minutes(self):
        low(self.conn, "cola-330")
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 50)
        self.assertEqual(self.round.call_count, 1)
        low(self.conn, "chips")
        self.clock.tick(self.conn, 110)   # one minute after the round: gathered, but too early
        self.clock.tick(self.conn, 170)
        self.assertEqual(self.round.call_count, 1)
        self.clock.tick(self.conn, 232)   # three minutes after the end of the round (about 50)
        self.assertEqual(self.round.call_count, 2)

    def test_a_failing_round_does_not_stop_the_clock(self):
        self.round.side_effect = RuntimeError("boom")
        low(self.conn, "cola-330")
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 50)
        self.assertEqual(self.round.call_count, 1)
        low(self.conn, "chips")
        self.clock.tick(self.conn, 300)
        self.clock.tick(self.conn, 350)
        self.assertEqual(self.round.call_count, 2)

    def test_events_older_than_today_are_ignored(self):
        append_event(self.conn, type="stock.low_detected", actor="system", at="2020-01-01T10:00:00.000Z",
                     payload={"product_id": "cola-330", "on_hand": 1, "per_day": 1, "days_left": 1, "source": "x"})
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 100)
        self.round.assert_not_called()

    def test_first_start_begins_at_the_last_event(self):
        low(self.conn, "cola-330")
        autorun._init_cursor(self.conn)
        self.clock.tick(self.conn, 0)
        self.clock.tick(self.conn, 100)
        self.round.assert_not_called()
        low(self.conn, "chips")
        self.clock.tick(self.conn, 101)
        self.clock.tick(self.conn, 150)
        self.assertEqual(self.round.call_count, 1)


class StartTest(unittest.TestCase):
    def test_autorun_zero_starts_nothing(self):
        with mock.patch.dict(os.environ, {"XANGX_AUTORUN": "0"}), \
                mock.patch.object(autorun.threading, "Thread") as thread:
            self.assertIsNone(autorun.start())
            thread.assert_not_called()

    def test_default_starts_one_daemon_thread(self):
        env = {k: v for k, v in os.environ.items() if k != "XANGX_AUTORUN"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(autorun.threading, "Thread") as thread:
            autorun.start()
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.return_value.start.assert_called_once()


class TriggerTest(unittest.TestCase):
    """handle_trigger itself, with the fake Claude."""

    def setUp(self):
        self.conn = new_db()
        patcher = mock.patch.dict(os.environ, {"XANGX_FAKE_LLM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_writes_woke_with_the_ids_and_no_owner_message(self):
        result = agent.handle_trigger(self.conn, "Η Coca-Cola έπεσε σε χαμηλό", [7, 9])
        events = read_events(self.conn)
        types = [e.type for e in events]
        self.assertNotIn("owner.said", types)
        for wanted in ("agent.woke", "agent.planned", "agent.tool_ran", "agent.said"):
            self.assertIn(wanted, types)
        woke = events[0]
        self.assertEqual(woke.type, "agent.woke")
        self.assertEqual(woke.payload, {"reason": "Η Coca-Cola έπεσε σε χαμηλό", "trigger_ids": [7, 9]})
        said = read_events(self.conn, types=["agent.said"])[0]
        self.assertEqual(said.payload["channel"], "auto")
        self.assertEqual(said.caused_by, woke.id)
        self.assertEqual(result["event_ids"], [e.id for e in events])

    def test_paused_runs_no_tool(self):
        append_event(self.conn, type="system.paused", actor="owner", payload={})
        result = agent.handle_trigger(self.conn, "κάτι", [1])
        types = types_of(self.conn)
        self.assertIn("agent.woke", types)
        self.assertNotIn("agent.tool_ran", types)
        self.assertNotIn("agent.planned", types)
        self.assertTrue(result["reply"].startswith("Είμαι σε παύση"))
        self.assertEqual(read_events(self.conn, types=["agent.said"])[0].payload["text"], result["reply"])

    def test_the_clock_does_not_run_tools_when_paused(self):
        low(self.conn)
        append_event(self.conn, type="system.paused", actor="owner", payload={})
        clock = autorun.Clock()
        clock.tick(self.conn, 0)
        clock.tick(self.conn, 50)
        types = types_of(self.conn)
        self.assertIn("agent.woke", types)
        self.assertNotIn("agent.tool_ran", types)

    def test_describe_mcp_plan_names_the_first_step(self):
        from xangx.screen.describe import describe

        plan = lambda steps: SimpleNamespace(type="agent.planned", payload={"steps": steps, "via": "mcp"})
        self.assertEqual(describe(plan([{"tool": "get_store_status", "summary": "Κατάσταση μαγαζιού"}])),
                         "Άλλος agent ζήτησε μέσω MCP: Κατάσταση μαγαζιού")
        self.assertEqual(describe(plan([{"tool": "get_store_status"}])), "Άλλος agent ζήτησε μέσω MCP: get_store_status")
        own = SimpleNamespace(type="agent.planned", payload={"steps": [{}, {}]})
        self.assertEqual(describe(own), "Σχεδίασα 2 βήματα")

    def test_describe_says_it_started_by_itself(self):
        from xangx.screen.describe import describe

        agent.handle_trigger(self.conn, "Η Coca-Cola έπεσε σε χαμηλό", [1])
        woke = read_events(self.conn, types=["agent.woke"])[0]
        self.assertEqual(describe(woke), "Ο συνεργάτης ξεκίνησε μόνος του: Η Coca-Cola έπεσε σε χαμηλό")


class SeedRoundTest(unittest.TestCase):
    """The whole path on the seeded shop: the camera sees Coca-Cola taken, the clock wakes the partner."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        data = root / "data"
        (data / "inbox").mkdir(parents=True)
        (data / "outbox").mkdir()
        env = mock.patch.dict(os.environ, {
            "XANGX_DB": str(data / "xangx.sqlite"), "XANGX_DATA_DIR": str(data),
            "XANGX_FILES": str(data / "files"), "XANGX_FAKE_LLM": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self.conn = connect()
        init_db(self.conn)
        self.addCleanup(self.conn.close)
        director.reset_demo(self.conn)
        autorun._init_cursor(self.conn)
        self.assertTrue(director.run_step("kamera", self.conn)["ok"])
        stock_worker.run_once(self.conn)
        self.low_ids = [e.id for e in read_events(self.conn, types=["stock.low_detected"])]
        self.assertTrue(self.low_ids)
        self.before = self.conn.execute("SELECT MAX(id) FROM events").fetchone()[0]  # the seed already has sent orders

    def new_types(self):
        return [e.type for e in read_events(self.conn, after_id=self.before)]

    def test_fake_claude_round_calls_a_tool_and_sends_nothing(self):
        clock = autorun.Clock()
        clock.tick(self.conn, 0)
        clock.tick(self.conn, 50)
        woke = read_events(self.conn, types=["agent.woke"])
        self.assertEqual(len(woke), 1)
        self.assertEqual(woke[0].payload["trigger_ids"], self.low_ids)
        self.assertIn("Coca-Cola", woke[0].payload["reason"])
        types = self.new_types()
        self.assertIn("agent.tool_ran", types)
        self.assertNotIn("order.sent", types)
        self.assertNotIn("owner.said", types)

    def test_an_order_from_the_round_ends_as_a_card_not_as_an_email(self):
        if "propose_order" not in tool_catalog.TOOLS:
            # test_master puts the registry back to a snapshot without the role tools, and an import does not register twice
            importlib.reload(importlib.import_module("xangx.orders.tools"))
        state = {"order_id": None}

        def tool_use(name, args):
            block = SimpleNamespace(type="tool_use", id=f"tu-{name}", name=name, input=args)
            return SimpleNamespace(stop_reason="tool_use", content=[block], stop_details=None)

        def respond(**kwargs):
            last = kwargs["messages"][-1]["content"]
            if isinstance(last, list) and last and last[0].get("type") == "tool_result":
                data = json.loads(last[0]["content"])
                if data.get("order_id") and not state["order_id"]:
                    state["order_id"] = data["order_id"]
                    return tool_use("send_order", {"order_id": data["order_id"]})
                return SimpleNamespace(
                    stop_reason="end_turn", stop_details=None,
                    content=[SimpleNamespace(type="text", text="Η παραγγελία περιμένει το ναι σου.")],
                )
            return tool_use("propose_order", {"supplier_id": "sup-ymittou"})

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(side_effect=respond))))
        with mock.patch.object(agent, "_get_client", return_value=client):
            clock = autorun.Clock()
            clock.tick(self.conn, 0)
            clock.tick(self.conn, 50)
        self.assertTrue(state["order_id"], "the model never got an order_id from propose_order")
        types = self.new_types()
        self.assertIn("order.proposed", types)
        self.assertIn("approval.requested", types)
        self.assertNotIn("order.sent", types)
        requested = read_events(self.conn, after_id=self.before, types=["approval.requested"])[0]
        self.assertEqual(requested.payload["tool"], "send_order")


if __name__ == "__main__":
    unittest.main()
