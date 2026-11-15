import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_screen_b1 import build_scene
from xangx.accountant import core as accountant
from xangx.demo.seed import seed_all
from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen import api_home
from xangx.screen.describe import describe, load_names
from xangx.shared.db import connect, init_db
from xangx.shared.events import Event, append_event, read_events

KEYS = {"sales", "stock", "order", "invoices", "payments", "undo", "iban", "last", "today",
        "credit", "last_before"}


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
        self.assertRegex(data["today"], r"^\d{4}-\d\d-\d\d$")
        self.assertEqual(
            set(data["sales"]),
            {"from", "to", "per_day", "total_cents", "receipts", "avg_cents", "yesterday",
             "same_day"})
        self.assertEqual(set(data["stock"]), {"low", "expiring"})
        self.assertEqual(set(data["invoices"]), {"need_you", "today", "docs", "recent", "month"})
        self.assertEqual(set(data["payments"]), {"next", "last_paid"})
        self.assertEqual(set(data["last_before"]), {"master", "stock", "orders", "invoices", "accountant"})
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
        # The home shows the latest proposal by time. After 06:45 the scene's morning run can
        # propose a newer order (P-0319) than the demo's P-0318, so the id depends on the clock.
        cancelled = {e.payload.get("order_id") for e in read_events(self.conn, types=["order.cancelled"])}
        latest = max((e for e in read_events(self.conn, types=["order.proposed"])
                      if e.payload.get("order_id") not in cancelled), key=lambda e: (e.at, e.id))
        order = self.home()["order"]
        self.assertEqual(order["order_id"], latest.payload["order_id"])
        if order["order_id"] == "P-0318":
            self.assertEqual(order["step"], "received")
        else:
            self.assertIn(order["step"], ("proposed", "sent", "received"))
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

    def _undo(self, event):
        append_event(self.conn, type="action.undone", actor="owner",
                     payload={"event_id": event.id, "how": "test"})

    def test_paid_ignores_undone_payment(self):
        base = read_events(self.conn, types=["payment.reminder_set"])[-1].payload
        append_event(self.conn, type="payment.reminder_set", actor="accountant",
                     subject="invoice:X-undone", idem_key="home-test:X-undone",
                     payload={"supplier_id": base["supplier_id"], "invoice_id": "X-undone",
                              "due_on": "2000-01-01", "amount_cents": 1000})
        paid, _ = append_event(self.conn, type="payment.recorded", actor="owner",
                               payload={"invoice_id": "X-undone", "method": "cash",
                                        "amount_cents": 1000})
        self.assertNotEqual(self.home()["payments"]["next"]["label"], "X-undone")
        self._undo(paid)
        self.assertEqual(self.home()["payments"]["next"]["label"], "X-undone")

    def test_credit_for_pending_approval(self):
        data = self.home()
        approval_id = self.ids["credit_approval"]
        c = data["credit"][approval_id]
        details = accountant.credit_details(self.conn, self.ids["Α-2271"])
        self.assertEqual((c["expected"], c["actual"], c["missing"]), (10, 9, 1))
        self.assertEqual(c["amount_cents"], details["amount_cents"])
        self.assertEqual(c["supplier"], details["supplier"]["name"])
        self.assertEqual(c["subject"], details["subject"])
        self.assertEqual(c["label"], "Α-2271")
        self.assertEqual(c["order_id"], "P-0318")
        self.assertEqual(c["more"], 0)
        self.assertTrue(c["product"])
        self.assertGreater(c["unit_cents"], 0)
        self.assertNotIn("email", c)
        self.assertNotIn("to", c)
        self.assertNotIn(details["supplier"]["email"], json.dumps(data, ensure_ascii=False))
        append_event(self.conn, type="approval.decided", actor="owner",
                     payload={"approval_id": approval_id, "decision": "no", "channel": "web"})
        self.assertEqual(self.home()["credit"], {})

    def _old_invoice(self, invoice_id, number, days_ago, total):
        # Just before the start of the Greek day `days_ago` days back: newer than the seed's
        # invoices of that day, which are read at about 06:00.
        now = datetime.now(timezone.utc)
        start = datetime.strptime(greek_day_start_utc(now), "%Y-%m-%dT%H:%M:%S.000Z")
        at = start.replace(tzinfo=timezone.utc) - timedelta(days=days_ago - 1, minutes=1)
        today = (datetime.now(timezone.utc) + greek_offset(datetime.now(timezone.utc))).date()
        append_event(self.conn, type="invoice.read", actor="system",
                     at=at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                     payload={"invoice_id": invoice_id, "supplier_afm": "000000000",
                              "series": "Τ", "number": number, "date": today.isoformat(),
                              "lines": [], "net_cents": total, "vat_by_rate": {},
                              "total_cents": total, "checks": {}, "confidence": 1})
        return at

    def test_recent_month_and_last_paid(self):
        yesterday = self._old_invoice("T-1", "7001", 1, 1000)
        before = self._old_invoice("T-2", "7002", 2, 2000)
        append_event(self.conn, type="payment.reminder_set", actor="accountant",
                     subject="invoice:T-1", idem_key="home-test:T-1",
                     payload={"supplier_id": "sup-ymittou", "invoice_id": "T-1",
                              "due_on": "2099-01-01", "amount_cents": 1000})
        append_event(self.conn, type="payment.recorded", actor="owner",
                     payload={"invoice_id": "T-1", "method": "cash", "amount_cents": 1000})
        data = self.home()
        recent = data["invoices"]["recent"]
        self.assertEqual(recent[0]["label"], "Τ-7001")
        self.assertLessEqual(len(recent), 3)
        dates = {r["label"]: r["date"] for r in recent}
        self.assertEqual(dates["Τ-7001"], (yesterday + greek_offset(yesterday)).date().isoformat())
        self.assertEqual(dates["Τ-7002"], (before + greek_offset(before)).date().isoformat())
        self.assertLess(dates["Τ-7002"], dates["Τ-7001"])
        self.assertEqual([r["date"] for r in recent], sorted((r["date"] for r in recent), reverse=True))
        month = data["invoices"]["month"]
        names = ("Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος", "Ιούλιος",
                 "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος")
        self.assertEqual(month["name"], names[int(data["today"][5:7]) - 1])
        self.assertGreaterEqual(month["count"], 2)
        self.assertGreaterEqual(month["total_cents"], 3000)
        last = data["payments"]["last_paid"]
        self.assertEqual((last["label"], last["amount_cents"]), ("Τ-7001", 1000))
        self.assertEqual(last["date"], data["today"])

    def test_last_before_today(self):
        self.assertIsNone(self.home()["last"]["master"])
        at = datetime.now(timezone.utc) - timedelta(days=1)
        append_event(self.conn, type="check.morning_done", actor="system",
                     at=at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), payload={"findings": []})
        data = self.home()
        self.assertEqual(data["last_before"]["master"],
                         {"date": (at + greek_offset(at)).strftime("%d/%m"),
                          "text": "Πρωινός έλεγχος"})
        self.assertIsNone(data["last_before"]["orders"])  # it worked today

    def _unread_flag(self, invoice_id):
        e, _ = append_event(self.conn, type="exception.flagged", actor="accountant",
                            subject=f"invoice:{invoice_id}", idem_key=f"invoice_unread_offline:{invoice_id}",
                            payload={"kind": "invoice_unread_offline", "ref": f"invoice:{invoice_id}",
                                     "note": "Δεν μπόρεσα να διαβάσω το τιμολόγιο: δεν υπάρχει σύνδεση."})
        return e

    def test_an_unread_flag_shows_until_the_invoice_is_read(self):
        flag = self._unread_flag("X-OFF-1")
        self.assertEqual(self.home()["last"]["accountant"]["text"], describe(flag, load_names(self.conn)))
        append_event(self.conn, type="invoice.read", actor="system",
                     payload={"invoice_id": "X-OFF-1", "supplier_afm": "000000000", "series": "Τ",
                              "number": "8001", "date": "2026-11-15", "lines": [], "net_cents": 100,
                              "vat_by_rate": {}, "total_cents": 100, "checks": {}, "confidence": 1})
        last = self.home()["last"]["accountant"]
        self.assertTrue(last is None or "διαβάσω" not in last["text"], last)

    def test_hidden_instructions_only_when_there_are_some(self):
        from xangx.screen import api_b1

        def ev(type_, payload):
            return Event(id=1, at="2026-11-15T06:00:00.000Z", store_id="store-1", actor="accountant",
                         type=type_, subject=None, caused_by=None, idem_key=None, payload=payload, v=1)
        read = ev("invoice.read", {"invoice_id": "X-1", "checks": {"arithmetic": True}})
        b = {"read": read, "prices": [], "flags": [ev("exception.flagged", {"kind": "new_iban", "note": "x"})]}
        self.assertNotIn("Κρυμμένες", api_b1._status(self.conn, "X-1", b, None)[1])
        b["flags"].append(ev("exception.flagged", {"kind": "hidden_instructions", "note": "x"}))
        self.assertIn("Κρυμμένες οδηγίες", api_b1._status(self.conn, "X-1", b, None)[1])


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


class PaperNamesTextTest(unittest.TestCase):
    """Manolis's findings of 15/11: the owner reads the paper's number, never INV- or D- codes."""
    NAMES = {"invoice": {"INV-aacc6ac9": "Α-2271", "INV-b2": "Α-2271β"}}

    def event(self, type_, payload, actor="accountant"):
        return Event(id=1, at="2026-11-15T06:00:00.000Z", store_id="store-1", actor=actor, type=type_,
                     subject=None, caused_by=None, idem_key=None, payload=payload, v=1)

    def test_reminder_names_the_paper(self):
        e = self.event("payment.reminder_set", {"supplier_id": 1, "invoice_id": "INV-aacc6ac9",
                                                 "due_on": "2026-12-15", "amount_cents": 15929})
        self.assertEqual(describe(e, self.NAMES), "Υπενθύμιση πληρωμής για το τιμολόγιο Α-2271")

    def test_delivery_with_one_shortage_is_singular_and_names_the_order(self):
        e = self.event("delivery.completed", {"delivery_id": "D-P-0318", "lines": [], "shortages": [{"x": 1}],
                                              "unknown_barcodes": []}, actor="stock")
        self.assertEqual(describe(e), "Τελείωσε η παραλαβή της P-0318, 1 έλλειψη")
        e.payload["shortages"] = [{"x": 1}, {"x": 2}]
        self.assertEqual(describe(e), "Τελείωσε η παραλαβή της P-0318, 2 ελλείψεις")

    def test_duplicate_names_both_papers(self):
        e = self.event("invoice.duplicate_suspected", {"invoice_id": "INV-b2", "other_invoice_id": "INV-aacc6ac9"},
                       actor="invoices")
        self.assertEqual(describe(e, self.NAMES), "Το τιμολόγιο Α-2271β μοιάζει διπλό με το Α-2271")


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
