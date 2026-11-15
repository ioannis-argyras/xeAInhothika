import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xangx.screen import api_c
from xangx.server import app as server_app
from xangx.shared.db import connect, init_db


def put(conn, at, actor, type_, payload):
    """Raw insert: only the system actor may set `at` through append_event."""
    conn.execute(
        "INSERT INTO events (at, store_id, actor, type, payload, v) VALUES (?,?,?,?,?,1)",
        (at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "store-1", actor, type_, json.dumps(payload)),
    )
    conn.commit()


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        app = FastAPI()
        app.include_router(api_c.router)
        self.client = TestClient(app)
        self.conn = connect()
        init_db(self.conn)
        start = server_app.athens_day_start_utc()
        self.midnight = datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_empty_day(self):
        data = self.client.get("/api/screen/replay").json()
        self.assertEqual(data["events"], [])
        self.assertIsNone(data["first_id"])
        self.assertIsNone(data["last_id"])

    def test_minute_is_greek_clock_and_quiet_events_are_left_out(self):
        # local midnight + 7 h is 07:00 in Greece, winter or summer time.
        put(self.conn, self.midnight + timedelta(hours=7), "master", "check.morning_done",
            {"findings": []})
        put(self.conn, self.midnight + timedelta(hours=7, minutes=5), "orders", "order.sent",
            {"order_id": "PO-1"})
        put(self.conn, self.midnight + timedelta(hours=8), "pos", "sale.recorded",
            {"total_cents": 100})
        put(self.conn, self.midnight + timedelta(hours=9), "owner", "owner.said",
            {"text": "γεια", "channel": "web"})
        put(self.conn, self.midnight + timedelta(hours=10), "owner", "approval.decided",
            {"approval_id": "A-1", "decision": "yes", "channel": "web"})
        data = self.client.get("/api/screen/replay").json()
        events = data["events"]
        self.assertEqual([e["hhmm"] for e in events], ["07:00", "07:05", "10:00"])
        self.assertEqual(events[0]["minute"], 420)
        self.assertEqual(events[1]["minute"], 425)
        self.assertEqual(events[0]["team"], "master")
        self.assertEqual(events[1]["team"], "orders")
        self.assertEqual(events[2]["team"], "owner")
        self.assertEqual(data["first_id"], events[0]["id"])
        self.assertEqual(data["last_id"], events[2]["id"])

    def test_events_carry_their_type_and_approvals_their_approval_id(self):
        put(self.conn, self.midnight + timedelta(hours=10), "master", "approval.requested",
            {"approval_id": "A-7", "summary": "Ζητάω πιστωτικό 2,43 €;", "action": "x", "exact": {},
             "why": "y"})
        put(self.conn, self.midnight + timedelta(hours=10, minutes=1), "owner", "approval.decided",
            {"approval_id": "A-7", "decision": "yes", "channel": "web"})
        put(self.conn, self.midnight + timedelta(hours=11), "master", "check.morning_done",
            {"findings": []})
        events = self.client.get("/api/screen/replay").json()["events"]
        self.assertEqual([e["type"] for e in events],
                         ["approval.requested", "approval.decided", "check.morning_done"])
        self.assertEqual(events[0]["approval_id"], "A-7")
        self.assertEqual(events[1]["approval_id"], "A-7")
        self.assertNotIn("approval_id", events[2])

    def test_winter_time_is_utc_plus_two(self):
        offset = server_app.athens_offset(datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(offset, timedelta(hours=2))

    def test_yesterday_is_not_in_the_replay(self):
        put(self.conn, self.midnight - timedelta(hours=1), "master", "check.morning_done",
            {"findings": []})
        self.assertEqual(self.client.get("/api/screen/replay").json()["events"], [])


if __name__ == "__main__":
    unittest.main()
