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
        "credit", "last_before", "just_now"}


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
        self.assertEqual(set(data["undo"]), {"can_total", "items", "hours", "kinds"})
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


    # ---- the additions for the demo moments #2, #5, #6, #9 (15/11/2026) ----

    def add(self, type_, actor, payload, **kw):
        event, _ = append_event(self.conn, type=type_, actor=actor, payload=payload, **kw)
        return event

    def camera(self, taken=2):
        return self.add("shelf.items_taken", "camera", {
            "position_id": "fridge1-r1", "product_id": "coffee-frappe-can", "taken": taken,
            "confidence": "high", "file_ref": "b.jpg", "prev_file_ref": "a.jpg"})

    def low(self, on_hand=1):
        return self.add("stock.low_detected", "stock", {
            "product_id": "coffee-frappe-can", "on_hand": on_hand, "per_day": 2.0, "days_left": 0.5,
            "source": "camera"})

    def price(self):
        rose = self.add("price.changed", "invoices", {
            "product_id": "cola-330", "supplier_id": "sup-ymittou", "old_cents": 57, "new_cents": 62,
            "pct": 8.8})
        cheaper = self.add("supplier.cheaper_found", "orders", {
            "product_id": "cola-330", "from_supplier_id": "sup-ymittou", "from_cents": 62,
            "to_supplier_id": "sup-kava", "to_cents": 58, "pct_change": 8.8,
            "caused_by_invoice_id": self.ids["Α-2271"]})
        return rose, cheaper

    def proposal(self, order_id="P-TEST1"):
        return self.add("order.proposed", "orders", {
            "order_id": order_id, "supplier_id": "sup-ymittou", "net_cents": 10140,
            "lines": [{"product_id": "coffee-frappe-can", "qty": 48, "unit_cents": 95}],
            "why": {"product_id": "coffee-frappe-can", "per_day": 2.0, "on_hand": 1, "lead_days": 1}})

    def text(self, item):
        return "".join(part["t"] for part in item["parts"])

    def test_short_names(self):
        self.assertEqual(api_home._short_product("Cold brew κουτάκι 250ml"), "Cold brew")
        self.assertEqual(api_home._short_product("Coca-Cola 330ml"), "Coca-Cola")
        self.assertEqual(api_home._short_product("Νερό 1,5L εξάδα"), "Νερό")
        self.assertEqual(api_home._short_product("330ml"), "330ml")
        self.assertEqual(api_home._short_supplier("Διανομές Υμηττού Α.Ε."), "Διανομές Υμηττού")
        self.assertEqual(api_home._short_supplier("Κάβα Βύρωνα"), "Κάβα Βύρωνα")
        self.assertEqual(api_home._short_position("Ψυγείο 1 · 2η σειρά"), "Ψυγείο 1")
        self.assertEqual(api_home._pct_text(8.8), "8,8%")
        self.assertEqual(api_home._pct_text(9.0), "9%")

    def test_the_camera_and_the_low_stock_are_one_line(self):
        camera = self.camera()
        self.low()
        feed = self.home()["just_now"]
        self.assertEqual(feed[0]["kind"], "shelf.items_taken")
        self.assertEqual(self.text(feed[0]), "Πήραν 2 Cold brew από το Ψυγείο 1. Έμεινε μόνο 1.")
        self.assertEqual((feed[0]["team"], feed[0]["icon"], feed[0]["who"]),
                         ("stock", "camera", "Αποθήκη, από την κάμερα"))
        self.assertRegex(feed[0]["hhmm"], r"^\d\d:\d\d$")
        self.assertEqual(feed[0]["hhmm"], api_home._hhmm(camera.at))
        self.assertNotIn("stock.low_detected", [i["kind"] for i in feed])

    def test_the_camera_alone_has_no_low_sentence(self):
        self.camera(taken=3)
        self.assertEqual(self.text(self.home()["just_now"][0]), "Πήραν 3 Cold brew από το Ψυγείο 1.")

    def test_a_price_rise_and_the_cheaper_supplier_are_one_line(self):
        self.price()
        feed = self.home()["just_now"]
        item = feed[0]
        self.assertEqual(item["kind"], "price.changed")
        self.assertEqual(item["parts"], [
            {"t": "Η Coca-Cola ακρίβυνε "}, {"t": "8,8%", "em": "bad"},
            {"t": " στον Διανομές Υμηττού."}, {"t": " Η Κάβα Βύρωνα την έχει "},
            {"t": "0,58 €", "em": "good"}, {"t": "."}])
        self.assertEqual((item["team"], item["icon"], item["who"]),
                         ("invoices", "trend-up", "Τιμολόγια, στο Α-2271"))
        self.assertNotIn("supplier.cheaper_found", [i["kind"] for i in feed])

    def test_only_four_lines_and_the_newest_first(self):
        self.camera()
        self.low()
        self.price()
        self.proposal()
        sent = self.add("order.sent", "orders", {"order_id": "P-TEST1", "to": "x@y.gr", "message_id": "m1"})
        feed = self.home()["just_now"]
        self.assertEqual(len(feed), 4)
        self.assertEqual(feed[0]["kind"], "order.sent")
        self.assertEqual(self.text(feed[0]), "Έστειλα την P-TEST1 στον Διανομές Υμηττού: 101,40 €.")
        self.assertEqual(feed[1]["kind"], "price.changed")
        self.assertEqual(feed[0]["hhmm"], api_home._hhmm(sent.at))

    def test_a_read_invoice_is_a_first_person_sentence(self):
        feed = self.home()["just_now"]
        reads = [i for i in feed if i["kind"] == "invoice.read"]
        self.assertTrue(reads)
        self.assertRegex(self.text(reads[0]), r"^Διάβασα το .+: \d+ γραμμ(ή|ές), [\d.]+,\d\d €\.$")

    def test_until_id_cuts_the_feed(self):
        before = self.camera()
        self.price()
        cut = self.home(until_id=before.id)["just_now"]
        self.assertEqual(cut[0]["kind"], "shelf.items_taken")
        self.assertNotIn("price.changed", [i["kind"] for i in cut])

    def test_an_undone_event_is_not_in_the_feed(self):
        paid = self.add("payment.recorded", "owner", {"invoice_id": "X-1", "method": "cash", "amount_cents": 100})
        self.assertEqual(self.home()["just_now"][0]["kind"], "payment.recorded")
        append_event(self.conn, type="action.undone", actor="owner", payload={"event_id": paid.id, "how": "test"})
        self.assertNotIn("payment.recorded", [i["kind"] for i in self.home()["just_now"]])

    def test_the_stock_tile_has_the_camera_first(self):
        self.camera()
        self.low()
        low = self.home()["stock"]["low"]
        self.assertEqual(low[0]["short"], "Cold brew")
        self.assertEqual(low[0]["camera"]["taken"], 2)
        self.assertRegex(low[0]["camera"]["hhmm"], r"^\d\d:\d\d$")
        self.assertEqual(low[0]["on_hand"], 1)
        self.assertEqual(low[0]["low"], 1)
        self.assertEqual(len([s for s in low if s["name"] == low[0]["name"]]), 1)

    def test_the_cause_of_an_order_proposal(self):
        camera = self.camera()
        self.low()
        self.proposal()
        order = self.home()["order"]
        self.assertEqual(order["step"], "proposed")
        hhmm = api_home._hhmm(camera.at)
        self.assertEqual(order["cause"]["hhmm"], hhmm)
        self.assertEqual(order["cause"]["parts"], [
            {"t": f"Στις {hhmm} η κάμερα είδε ότι "}, {"t": "πήραν 2 Cold brew", "em": "b"},
            {"t": " από το Ψυγείο 1. Έμεινε μόνο 1."}])
        self.add("order.sent", "orders", {"order_id": "P-TEST1", "to": "x@y.gr", "message_id": "m1"})
        self.assertIsNone(self.home()["order"]["cause"])

    def test_no_cause_without_the_camera(self):
        self.proposal()
        self.assertIsNone(self.home()["order"]["cause"])

    def test_the_suppliers_reply(self):
        self.proposal()
        self.assertIsNone(self.home()["order"]["reply"])
        self.add("order.sent", "orders", {"order_id": "P-TEST1", "to": "x@y.gr", "message_id": "m1"})
        reply = self.add("order.reply_received", "orders", {
            "order_id": "P-TEST1", "summary": "Επιβεβαίωσε για αύριο", "file_ref": "r.eml"})
        data = self.home()
        self.assertEqual(data["order"]["reply"], {"summary": "Επιβεβαίωσε για αύριο",
                                                  "hhmm": api_home._hhmm(reply.at)})
        self.assertEqual(self.text(data["just_now"][0]),
                         "Ο προμηθευτής επιβεβαίωσε την παραγγελία P-TEST1 για αύριο.")
        self.assertEqual(data["just_now"][0]["icon"], "envelope-open")
        self.add("order.reply_received", "orders", {
            "order_id": "P-TEST1", "summary": "Δεν έχουμε το προϊόν.", "file_ref": "r2.eml"})
        self.assertEqual(self.text(self.home()["just_now"][0]),
                         "Ο προμηθευτής απάντησε για την P-TEST1: Δεν έχουμε το προϊόν.")

    def test_the_agents_footer_is_short(self):
        taken = self.camera()
        self.add("agent.woke", "master", {"reason": "Η κάμερα είδε ότι πήραν 2 Cold brew και μένει 1.",
                                          "trigger_ids": [taken.id]})
        self.assertEqual(self.home()["last"]["master"]["text"], "Είδε χαμηλό απόθεμα στο ράφι")
        rose, cheaper = self.price()
        self.add("agent.woke", "master", {"reason": "Μια πολύ μεγάλη φράση που κόβεται",
                                          "trigger_ids": [rose.id, cheaper.id]})
        self.assertEqual(self.home()["last"]["master"]["text"], "Είδε αλλαγή τιμής στο Α-2271")

    def test_undo_hours_and_kinds(self):
        undo = self.home()["undo"]
        self.assertEqual(undo["hours"], 24)
        self.assertEqual([k["label"] for k in undo["kinds"]], [
            "Πληρωμές και υπενθυμίσεις πληρωμής", "Κινήσεις αποθέματος", "Προτάσεις παραγγελίας"])
        self.assertEqual([k["team"] for k in undo["kinds"]], ["accountant", "stock", "orders"])
        self.assertEqual([k["icon"] for k in undo["kinds"]], ["calculator", "package", "truck"])

    def test_no_markup_in_any_new_text(self):
        self.camera()
        self.low()
        self.price()
        self.proposal()
        self.add("order.reply_received", "orders", {"order_id": "P-TEST1", "summary": "Επιβεβαίωσε", "file_ref": "r.eml"})
        data = self.home()
        new = json.dumps([data["just_now"], data["order"], data["stock"], data["undo"], data["last"]],
                         ensure_ascii=False)
        self.assertNotRegex(new, r"[<>]")



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

    def test_credit_request_names_the_paper(self):
        e = self.event("credit.requested", {"invoice_id": "INV-aacc6ac9"}, actor="invoices")
        self.assertEqual(describe(e, self.NAMES), "Ζητήθηκε πιστωτικό για το τιμολόγιο Α-2271")

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
                self.assertEqual(data["undo"]["items"], [])
                self.assertEqual(data["undo"]["can_total"], 0)
                self.assertEqual(data["undo"]["hours"], 24)
                self.assertEqual(len(data["undo"]["kinds"]), 3)
                self.assertEqual(data["just_now"], [])
                self.assertEqual(data["sales"]["total_cents"], 0)
                self.assertIsNone(data["sales"]["same_day"]["pct"])


if __name__ == "__main__":
    unittest.main()
