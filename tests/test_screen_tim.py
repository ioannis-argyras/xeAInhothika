import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_screen_b1 import build_scene
from xangx.accountant import core as accountant
from xangx.demo.seed import seed_all
from xangx.master.tools import greek_offset
from xangx.orders import worker as orders_worker
from xangx.screen import api_b1, api_tim
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events

PAGE_KEYS = {"today", "counts", "need", "ok_today", "payments", "months", "trend_ref",
             "month_folder", "last_id", "paused", "now"}
ROW_KEYS = {"invoice_id", "label", "supplier", "arrived", "lines", "level", "status",
            "total_cents", "pay"}
DETAIL_KEYS = {"via", "credit_box", "payment", "history", "vat"}
EMAIL = r"[\w.+-]+@[\w-]+\.[\w.]+"
IBAN = r"[A-Z]{2}\d{2}\s?[A-Z0-9]{4}"


class ScreenTimTest(unittest.TestCase):
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
        app.include_router(api_b1.router)
        app.include_router(api_tim.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.conn.close()

    def page(self, **params):
        r = self.client.get("/api/screen/invoices-page", params=params)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def detail(self, key, **params):
        r = self.client.get("/api/screen/invoices/" + self.ids[key], params=params)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _undo(self, event):
        append_event(self.conn, type="action.undone", actor="owner",
                     payload={"event_id": event.id, "how": "test"})

    def _pay(self, key, method="cash"):
        paid, _ = append_event(self.conn, type="payment.recorded", actor="owner",
                               payload={"invoice_id": self.ids[key], "method": method,
                                        "amount_cents": 5170})
        return paid

    def _row(self, data, key):
        for group in data["months"]:
            for row in group["rows"]:
                if row["invoice_id"] == self.ids[key]:
                    return row
        self.fail("row not found: " + key)

    # ---------- the page ----------

    def test_shape(self):
        data = self.page()
        self.assertEqual(set(data), PAGE_KEYS)
        self.assertEqual(set(data["counts"]), {"all", "suppliers", "today", "need_you"})
        self.assertEqual(set(data["payments"]), {"total_cents", "until", "items"})
        self.assertEqual(set(data["month_folder"]), {"name", "count", "total_cents"})
        self.assertEqual(data["months"][0]["key"], "today")
        for group in data["months"]:
            self.assertEqual(set(group), {"key", "name", "count", "total_cents", "rows"})
            self.assertEqual(group["count"], len(group["rows"]))
            for row in group["rows"]:
                self.assertEqual(set(row), ROW_KEYS)
                self.assertEqual(set(row["arrived"]), {"date", "at", "via"})
        self.assertRegex(data["now"], r"^\d\d:\d\d$")

    def test_counts(self):
        data = self.page()
        reads = read_events(self.conn, types=["invoice.read"])
        self.assertEqual(data["counts"]["all"], len({e.payload["invoice_id"] for e in reads}))
        self.assertEqual(data["counts"]["suppliers"], len({e.payload["supplier_afm"] for e in reads}))
        self.assertEqual(data["counts"]["today"], data["months"][0]["count"])
        today_rows = data["months"][0]["rows"]
        self.assertEqual(data["counts"]["need_you"],
                         sum(1 for r in today_rows if r["level"] != "ok"))
        self.assertEqual(data["ok_today"],
                         [{"label": "Γ-318", "supplier": "Γαλακτοκομικά Κορωπίου"}])

    def test_need_credit_first_without_email(self):
        import re
        data = self.page()
        first = data["need"][0]
        details = accountant.credit_details(self.conn, self.ids["Α-2271"])
        self.assertEqual(first["kind"], "credit")
        self.assertEqual(first["approval_id"], self.ids["credit_approval"])
        self.assertEqual((first["label"], first["missing"], first["order_id"]),
                         ("Α-2271", 1, "P-0318"))
        self.assertEqual(first["amount_cents"], details["amount_cents"])
        self.assertEqual(first["supplier"], details["supplier"]["name"])
        self.assertTrue(first["product"])
        self.assertEqual([n["kind"] for n in data["need"]], ["credit", "iban", "pay"])
        text = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(details["supplier"]["email"], text)
        self.assertIsNone(re.search(EMAIL, text))

    def test_need_iban_and_pay(self):
        import re
        data = self.page()
        iban = data["need"][1]
        self.assertEqual((iban["label"], iban["invoice_id"]), ("Β-512", self.ids["Β-512"]))
        self.assertEqual(iban["new_last4"], "7703")
        self.assertRegex(iban["known_last4"], r"^\d{4}$")
        self.assertIsNone(re.search(IBAN, json.dumps(data, ensure_ascii=False)))
        pay = data["need"][2]
        self.assertEqual((pay["label"], pay["amount_cents"]), ("Β-512", 5170))
        self.assertTrue(pay["how"].startswith("λήγει "))

    def test_pay_paid_then_undone(self):
        row = self._row(self.page(), "Β-512")
        self.assertEqual(row["pay"]["state"], "due")
        self.assertEqual(row["pay"]["days"], 0)
        paid = self._pay("Β-512")
        data = self.page()
        now = datetime.now(timezone.utc)
        today = (now + greek_offset(now)).date()
        self.assertEqual(self._row(data, "Β-512")["pay"],
                         {"state": "paid", "date": today.isoformat()})
        self.assertEqual(data["payments"]["items"], [])
        self.assertEqual([n["kind"] for n in data["need"]], ["credit", "iban"])
        self._undo(paid)
        data = self.page()
        self.assertEqual(self._row(data, "Β-512")["pay"]["state"], "due")
        self.assertEqual(len(data["payments"]["items"]), 1)

    def test_not_yet_is_not_paid(self):
        self._pay("Β-512", "not_yet")
        self.assertEqual(self._row(self.page(), "Β-512")["pay"]["state"], "due")

    def test_payments_items_sorted_and_asked(self):
        base = read_events(self.conn, types=["payment.reminder_set"])[-1].payload
        append_event(self.conn, type="payment.reminder_set", actor="accountant",
                     subject="invoice:T-far", idem_key="tim-test:far",
                     payload={"supplier_id": base["supplier_id"], "invoice_id": "T-far",
                              "due_on": "2099-01-01", "amount_cents": 1000})
        append_event(self.conn, type="payment.asked", actor="accountant",
                     subject="invoice:T-cash", idem_key="tim-test:cash",
                     payload={"invoice_id": "T-cash", "supplier_id": base["supplier_id"],
                              "amount_cents": 700, "invoice_date": "2026-11-15"})
        data = self.page()
        pays = data["payments"]
        due = [i["due_on"] for i in pays["items"]]
        self.assertEqual(due, sorted(due))
        self.assertEqual(pays["until"], "2099-01-01")
        self.assertEqual(pays["total_cents"], 5170 + 1000 + 700)
        cash = [i for i in pays["items"] if i["invoice_id"] == "T-cash"][0]
        self.assertEqual((cash["days"], cash["due_on"]), (0, data["today"]))
        far = [i for i in pays["items"] if i["invoice_id"] == "T-far"][0]
        self.assertGreater(far["days"], 1000)
        reminder = [e for e in read_events(self.conn, types=["payment.reminder_set"])
                    if e.payload["invoice_id"] == "T-far"][0]
        self._undo(reminder)
        self.assertNotIn("T-far", [i["invoice_id"] for i in self.page()["payments"]["items"]])

    def _old_invoice(self, invoice_id, number, paper_date, source=None):
        if source:
            append_event(self.conn, type="invoice.received", actor="system",
                         at="2026-10-02T08:00:00.000Z",
                         subject=f"invoice:{invoice_id}", idem_key=f"tim-test:{invoice_id}",
                         payload={"invoice_id": invoice_id, "source": source,
                                  "file_ref": "files/x.pdf", "sha256": "x"})
        append_event(self.conn, type="invoice.read", actor="system", at="2026-10-02T08:05:00.000Z",
                     payload={"invoice_id": invoice_id, "supplier_afm": "000000000",
                              "series": "Τ", "number": number, "date": paper_date,
                              "lines": [{"name": "x", "qty": 1}], "net_cents": 1000,
                              "vat_by_rate": {"24": 240}, "total_cents": 1240, "checks": {},
                              "confidence": 1})

    def test_months_newest_first_and_today_once(self):
        self._old_invoice("T-1", "9001", "2026-09-05", source="email")
        self._old_invoice("T-2", "9002", "2026-09-20", source="photo")
        data = self.page()
        keys = [g["key"] for g in data["months"]]
        self.assertEqual(keys[0], "today")
        self.assertEqual(keys[1:], sorted(keys[1:], reverse=True))
        sept = data["months"][keys.index("2026-09")]
        self.assertEqual(sept["name"], "Σεπτέμβριος 2026")
        self.assertEqual([r["label"] for r in sept["rows"]], ["Τ-9002", "Τ-9001"])
        self.assertEqual((sept["count"], sept["total_cents"]), (2, 2480))
        self.assertEqual([r["arrived"]["via"] for r in sept["rows"]], ["photo", "email"])
        self.assertEqual(sept["rows"][0]["arrived"]["date"], "2026-10-02")
        self.assertEqual(sept["rows"][0]["lines"], 1)
        ids = [r["invoice_id"] for g in data["months"] for r in g["rows"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(data["months"][1]["key"], "2026-11")
        self.assertEqual(data["months"][1]["name"], "Νοέμβριος 2026")
        self.assertEqual(data["month_folder"]["count"],
                         sum(g["count"] for g in data["months"]
                             if g["key"] in ("today", data["today"][:7])))
        self.assertEqual(data["month_folder"]["name"], "Νοέμβριος")

    def test_row_level_need_for_credit_and_iban(self):
        data = self.page()
        self.assertEqual(self._row(data, "Α-2271")["level"], "need")
        self.assertEqual(self._row(data, "Β-512")["level"], "need")
        self.assertEqual(self._row(data, "Γ-318")["level"], "ok")
        self.assertEqual(self._row(data, "Γ-318")["status"], "Όλα σωστά")

    def test_until_id_cuts(self):
        cut = self.page(until_id=self.ids["before_yes"])
        self.assertEqual(cut["need"], [])
        self.assertEqual(cut["counts"]["today"], 0)
        self.assertTrue(all(g["key"] != "today" for g in cut["months"]))

    # ---------- the detail ----------

    def test_detail_old_fields_stay_and_new_ones_come(self):
        d = self.detail("Α-2271")
        self.assertTrue(DETAIL_KEYS <= set(d))
        for key in ("invoice_id", "label", "supplier", "received_at", "status", "level", "read",
                    "lines", "checks", "credit", "date", "order_id", "net_text", "total_text"):
            self.assertIn(key, d)

    def test_detail_credit_box_and_history(self):
        d = self.detail("Α-2271")
        need = self.page()["need"][0]
        box = d["credit_box"]
        self.assertEqual(box["approval_id"], self.ids["credit_approval"])
        self.assertEqual((box["missing"], box["amount_cents"]), (1, need["amount_cents"]))
        self.assertNotIn("email", box)
        self.assertEqual(d["history"][-1], {"at": "τώρα", "team": "you", "kind": "you",
                                            "text": "Περιμένει εσένα: πιστωτικό 2,43 €"})
        first = d["history"][0]
        self.assertEqual((first["team"], first["text"]), ("invoices", "Ήρθε τιμολόγιο Α-2271"))
        self.assertRegex(first["at"], r"^\d\d:\d\d$")
        for item in d["history"]:
            self.assertNotIn("INV-", item["text"])
            self.assertIn(item["team"], ("invoices", "stock", "orders", "accountant", "master",
                                         "owner", "you", "agent"))
        self.assertIsNone(d["payment"])
        append_event(self.conn, type="approval.decided", actor="owner",
                     payload={"approval_id": self.ids["credit_approval"], "decision": "no",
                              "channel": "web"})
        d = self.detail("Α-2271")
        self.assertIsNone(d["credit_box"])
        self.assertNotEqual(d["history"][-1]["team"], "you")

    def test_detail_vat_sorted_with_int_rates(self):
        vat = self.detail("Α-2271")["vat"]
        self.assertEqual([v["rate"] for v in vat], [24, 13])
        self.assertTrue(all(isinstance(v["rate"], int) and isinstance(v["cents"], int)
                            for v in vat))
        self.assertEqual((vat[0]["cents"], vat[1]["cents"]), (2219, 514))

    def test_detail_payment_days_undo_and_paid(self):
        d = self.detail("Β-512")
        pay = d["payment"]
        self.assertEqual(set(pay), {"due_on", "days", "terms_days", "reminder_at", "event_id",
                                    "undo", "paid"})
        self.assertEqual((pay["days"], pay["undo"], pay["paid"]), (0, "can", None))
        reminder = read_events(self.conn, types=["payment.reminder_set"])[-1]
        self.assertEqual((pay["event_id"], pay["due_on"]),
                         (reminder.id, reminder.payload["due_on"]))
        self.assertEqual(d["history"][-1]["team"], "accountant")
        paid = self._pay("Β-512", "cash")
        pay = self.detail("Β-512")["payment"]
        self.assertEqual(pay["paid"]["method"], "cash")
        self.assertEqual(pay["days"], 0)
        self.assertEqual(self.detail("Β-512")["history"][-1]["team"], "owner")
        self._undo(reminder)
        pay = self.detail("Β-512")["payment"]
        self.assertEqual((pay["due_on"], pay["days"], pay["event_id"], pay["undo"]),
                         (None, None, None, None))
        self.assertEqual((pay["terms_days"], pay["reminder_at"]), (None, None))
        self.assertEqual(pay["paid"]["method"], "cash")
        self._undo(paid)
        self.assertIsNone(self.detail("Β-512")["payment"])

    def _line(self, d, start):
        return next(l for l in d["lines"] if l["name"].startswith(start))

    def test_detail_line_has_product_id_and_cheaper_only_when_found(self):
        d = self.detail("Α-2271")
        self.assertEqual(self._line(d, "Coca-Cola")["product_id"], "cola-330")
        self.assertTrue(all("cheaper" not in l for l in d["lines"]))  # nothing found yet
        orders_worker.run_once(self.conn)
        d = self.detail("Α-2271")
        self.assertEqual(self._line(d, "Coca-Cola")["cheaper"],
                         {"supplier": "Κάβα Βύρωνα", "cents": 58})
        self.assertEqual([l["name"] for l in d["lines"] if "cheaper" in l],
                         ["Coca-Cola 330ml κουτί"])
        self.assertTrue(all("cheaper" not in l for l in self.detail("Γ-318")["lines"]))

    def test_detail_check_values(self):
        d = self.detail("Α-2271")
        by_text = {c["text"]: c["value"] for c in d["checks"]}
        self.assertTrue(all("value" in c for c in d["checks"]))
        self.assertEqual(by_text["Ποσά και ΦΠΑ σωστά"], "131,96 + 27,33")
        self.assertEqual(by_text["ΑΦΜ προμηθευτή γνωστό"], "Διανομές Υμηττού Α.Ε.")
        self.assertEqual(by_text["Σελίδες"], "όλες")
        self.assertEqual(by_text["IBAN γνωστό"], "…0101")
        receipt = next(c for c in d["checks"] if c.get("lead", "").startswith("Παραλαβή"))
        self.assertEqual((receipt["ok"], receipt["text"], receipt["value"]),
                         (False, "ήρθαν 9 από 10", "λείπει 1"))
        order = [c["text"] for c in d["checks"]][:3]
        self.assertEqual(order, ["Ποσά και ΦΠΑ σωστά", "ΑΦΜ προμηθευτή γνωστό", "Σελίδες"])

    def test_detail_check_value_mydata_env_and_no_value_on_error(self):
        d = self.detail("Α-2271")
        self.assertEqual(next(c["value"] for c in d["checks"] if c["text"].startswith("myDATA")), "")
        append_event(self.conn, type="invoice.mydata_checked", actor="invoices",
                     payload={"invoice_id": self.ids["Α-2271"], "env": "dev", "found": True,
                              "totals_match": True})
        d = self.detail("Α-2271")
        mydata = next(c for c in d["checks"] if c["text"].startswith("myDATA"))
        self.assertEqual((mydata["ok"], mydata["value"]), (True, "dev"))

    def test_detail_payment_terms_and_reminder_time(self):
        d = self.detail("Α-2271")
        self.assertIsNone(d["payment"])
        accountant.set_payment_reminder(self.conn, self.ids["Α-2271"])
        pay = self.detail("Α-2271")["payment"]
        self.assertEqual(pay["terms_days"], 30)
        reminder = read_events(self.conn, types=["payment.reminder_set"])[-1]
        local = datetime.strptime(reminder.at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        local = local + greek_offset(local)
        self.assertEqual(pay["reminder_at"], local.strftime("%H:%M"))
        # a paper that is due the day it is dated has no credit terms to show
        self.assertIsNone(self.detail("Β-512")["payment"]["terms_days"])

    def test_detail_history_kind(self):
        kinds = [h["kind"] for h in self.detail("Α-2271")["history"]]
        self.assertEqual(kinds[:3], ["invoice.received", "invoice.read", "invoice.matched"])
        self.assertIn("price.changed", kinds)
        self.assertEqual(kinds[-1], "you")
        self.assertTrue(all(isinstance(k, str) and k for k in kinds))

    def test_page_trend_ref_points_at_the_invoice_of_the_biggest_change(self):
        ref = self.page()["trend_ref"]
        self.assertEqual(ref, {"invoice_id": self.ids["Α-2271"], "label": "Α-2271"})

    def test_page_trend_ref_none_without_changes(self):
        first = read_events(self.conn, types=["price.changed"])[0]
        self.assertIsNone(self.page(until_id=first.id - 1)["trend_ref"])

    def test_detail_iban_only_last4(self):
        import re
        text = json.dumps(self.detail("Β-512"), ensure_ascii=False)
        self.assertIsNone(re.search(IBAN, text))
        self.assertIn("7703", text)

    def test_detail_via_and_unknown(self):
        self._old_invoice("T-3", "9003", "2026-09-01", source="email")
        r = self.client.get("/api/screen/invoices/T-3")
        self.assertEqual(r.json()["via"], "email")
        self.assertEqual(self.client.get("/api/screen/invoices/NOPE-1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
