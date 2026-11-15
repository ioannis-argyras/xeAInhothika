import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_screen_b1 import build_scene
from xangx.demo.seed import seed_all
from xangx.screen import api_home
from xangx.screen.describe import describe
from xangx.shared.db import connect, init_db
from xangx.shared.events import Event, append_event, read_events

KEYS = {"sales", "stock", "order", "invoices", "payments", "undo", "iban", "last"}


class ScreenHomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.env = mock.patch.dict(os.environ, {
            "XANGX_DATA_DIR": cls.dir, "XANGX_FAKE_LLM": "1",
            "XANGX_FILES": os.path.join(cls.dir, "files"), "MYDATA_USER_ID": "",
            "MYDATA_SUBSCRIPTION_KEY": "", "MYDATA_SUPPLIER_USER_ID": "",
            "MYDATA_SUPPLIER_SUBSCRIPTION_KEY": "", "XANGX_DEMO_SUPPLIER_EMAIL": "",
            "SMTP_HOST": ""})
        cls.env.start()
        cls.scene_db = os.path.join(cls.dir, "scene.sqlite")
        os.environ["XANGX_DB"] = cls.scene_db
        conn = connect(cls.scene_db)
        init_db(conn)
        seed_all(conn)
        cls.ids = build_scene(conn, cls.dir)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.dir, "t.sqlite")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db + suffix)
            except FileNotFoundError:
                pass
        src, dst = connect(self.scene_db), connect(self.db)
        src.backup(dst)
        src.close()
        dst.close()
        os.environ["XANGX_DB"] = self.db
        self.conn = connect(self.db)
        app = FastAPI()
        app.include_router(api_home.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.conn.close()

    def home(self, **params):
        r = self.client.get("/api/screen/home", params=params)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_all_the_keys_are_there(self):
        data = self.home()
        self.assertEqual(set(data), KEYS)
        self.assertEqual(
            set(data["sales"]),
            {"from", "to", "per_day", "total_cents", "receipts", "avg_cents", "yesterday",
             "same_day"})
        self.assertEqual(set(data["stock"]), {"low", "expiring"})
        self.assertEqual(set(data["invoices"]), {"need_you", "today", "docs"})
        self.assertEqual(set(data["undo"]), {"can_total", "items"})
        self.assertEqual(set(data["last"]), {"master", "stock", "orders", "invoices", "accountant"})

    def test_sales_cover_thirty_days_without_today(self):
        sales = self.home()["sales"]
        self.assertEqual(len(sales["per_day"]), 30)
        self.assertEqual(sales["total_cents"], sum(d["total_cents"] for d in sales["per_day"]))
        self.assertEqual(sales["yesterday"], sales["per_day"][-1])
        self.assertEqual(sales["to"], sales["per_day"][-1]["date"])
        same = sales["same_day"]
        weekday = sales["yesterday"]["weekday"]
        self.assertEqual(
            same["prev_cents"],
            [d["total_cents"] for d in sales["per_day"][:-1] if d["weekday"] == weekday])
        self.assertIn(same["plural"], api_home.PLURAL_DAYS)
        if len(same["prev_cents"]) >= 2 and sum(same["prev_cents"]):
            mean = sum(same["prev_cents"]) / len(same["prev_cents"])
            self.assertEqual(same["pct"], round((sales["yesterday"]["total_cents"] - mean) / mean * 100))

    def test_the_order_of_the_scene(self):
        order = self.home()["order"]
        self.assertEqual(order["order_id"], "P-0318")
        self.assertEqual(order["step"], "received")
        self.assertTrue(order["supplier"])
        self.assertRegex(order["at"], r"^\d\d:\d\d$")

    def test_undo_items_are_sorted_by_the_due_day(self):
        base = read_events(self.conn, types=["payment.reminder_set"])[-1].payload
        for iid, due in (("X-late", "2099-01-20"), ("X-soon", "2099-01-02")):
            append_event(self.conn, type="payment.reminder_set", actor="accountant",
                         subject=f"invoice:{iid}", idem_key=f"home-test:{iid}",
                         payload={"supplier_id": base["supplier_id"], "invoice_id": iid,
                                  "due_on": due, "amount_cents": 1000})
        undo = self.home()["undo"]
        due_days = [i["days"] for i in undo["items"]]
        self.assertGreaterEqual(len(due_days), 3)
        self.assertEqual(due_days, sorted(due_days))
        self.assertGreaterEqual(undo["can_total"], len(undo["items"]))
        self.assertLessEqual(len(undo["items"]), 3)

    def test_payment_next_is_the_nearest_reminder(self):
        data = self.home()
        nxt = data["payments"]["next"]
        self.assertTrue(nxt["label"])
        self.assertEqual(nxt["due_on"], min(
            e.payload["due_on"] for e in read_events(self.conn, types=["payment.reminder_set"])))

    def test_no_iban_longer_than_four_digits(self):
        data = self.home()
        self.assertTrue(data["iban"])
        for row in data["iban"]:
            for key in ("known_last4", "new_last4"):
                self.assertFalse(re.search(r"\d{5,}", str(row[key])), row)
        self.assertNotRegex(json.dumps(data, ensure_ascii=False), r"[A-Z]{2}\d{2}\s?[A-Z0-9]{4}")

    def test_until_id_cuts_events(self):
        full = self.home()
        before_order = self.ids["before_yes"]
        cut = self.home(until_id=before_order)
        self.assertIsNotNone(full["order"])
        self.assertNotEqual(full["order"], cut["order"])
        self.assertEqual(cut["iban"], [])
        self.assertEqual(cut["invoices"]["today"], 0)

    def test_the_last_job_of_the_team(self):
        last = self.home()["last"]
        self.assertRegex(last["orders"]["at"], r"^\d\d:\d\d$")
        self.assertTrue(last["orders"]["text"])


class MonthFolderTextTest(unittest.TestCase):
    def event(self, month, count, total):
        return Event(id=1, at="2026-11-15T00:16:00.000Z", store_id="store-1", actor="accountant",
                     type="report.month_created", subject=None, caused_by=None, idem_key=None,
                     payload={"month": month, "invoice_count": count, "total_cents": total,
                              "file_ref": "x.pdf"}, v=1)

    def test_month_in_genitive_with_count_and_amount(self):
        self.assertEqual(describe(self.event("2026-11", 12, 123456)),
                         "Φάκελος Νοεμβρίου: 12 τιμολόγια, 1.234,56 €")

    def test_one_invoice(self):
        self.assertEqual(describe(self.event("2026-05", 1, 500)), "Φάκελος Μαΐου: 1 τιμολόγιο, 5,00 €")


class EmptyDatabaseTest(unittest.TestCase):
    def test_empty_database_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XANGX_DB": os.path.join(tmp, "e.sqlite")}):
                app = FastAPI()
                app.include_router(api_home.router)
                data = TestClient(app).get("/api/screen/home").json()
                self.assertEqual(set(data), KEYS)
                self.assertIsNone(data["order"])
                self.assertIsNone(data["payments"]["next"])
                self.assertEqual(data["undo"], {"can_total": 0, "items": []})
                self.assertEqual(data["sales"]["total_cents"], 0)
                self.assertIsNone(data["sales"]["same_day"]["pct"])


if __name__ == "__main__":
    unittest.main()
