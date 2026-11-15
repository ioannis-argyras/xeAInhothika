import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xangx.accountant import core as acc
from xangx.accountant import trends as tr
from xangx.accountant import worker
from xangx.demo.catalog_data import SUPPLIER_BY_ID, make_iban
from xangx.demo.seed import seed_reference
from xangx.invoices import core as inv
from xangx.shared import append_event, connect, init_db, read_events

Y = SUPPLIER_BY_ID["sup-ymittou"]


def _data(number="2271", iban=None, total_delta=0, payment=None):
    lines = [{"name": "Coca-Cola 330ml κουτί", "qty": 48, "unit_cents": 57, "net_cents": 2736,
              "vat_rate": 24},
             {"name": "Νερό 1,5L εξάδα", "qty": 10, "unit_cents": 215, "net_cents": 2150,
              "vat_rate": 13}]
    vat = {"24": 657, "13": 280}
    return {"supplier_afm": Y["afm"], "series": "Α", "number": number, "date": "2026-11-14",
            "lines": lines, "net_cents": 4886, "vat_by_rate": vat,
            "total_cents": 4886 + 937 + total_delta, "iban": iban or Y["iban"],
            "pages": {"seen": 1, "total": 1}, "hidden_instructions": None,
            "payment": payment}


class Accountant(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DATA_DIR": self.tmp, "XANGX_FAKE_LLM": "1", "MYDATA_USER_ID": "",
            "MYDATA_SUBSCRIPTION_KEY": "", "MYDATA_SUPPLIER_USER_ID": "",
            "MYDATA_SUPPLIER_SUBSCRIPTION_KEY": "", "XANGX_DEMO_SUPPLIER_EMAIL": "",
            "SMTP_HOST": "", "XANGX_FILES": str(Path(self.tmp) / "files")})
        self.env.start()
        self.conn = connect(":memory:")
        init_db(self.conn)
        seed_reference(self.conn)
        s = dict(actor="system")
        append_event(self.conn, type="order.proposed", payload={
            "order_id": "P-0318", "supplier_id": "sup-ymittou", "net_cents": 4886, "why": {},
            "lines": [{"product_id": "cola-330", "qty": 48, "unit_cents": 57},
                      {"product_id": "water-1500x6", "qty": 10, "unit_cents": 215}]}, **s)
        append_event(self.conn, type="delivery.started", payload={
            "delivery_id": "D-1", "order_id": "P-0318", "supplier_id": "sup-ymittou"}, **s)
        append_event(self.conn, type="delivery.completed", payload={
            "delivery_id": "D-1", "shortages": [], "unknown_barcodes": [],
            "lines": [{"product_id": "cola-330", "expected": 48, "scanned": 48},
                      {"product_id": "water-1500x6", "expected": 10, "scanned": 9}]}, **s)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _invoice(self, content=b"x", **kw):
        inbox = Path(self.tmp) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "a.pdf").write_bytes(content)
        [iid] = inv.scan_inbox(self.conn)
        inv.process_invoice(self.conn, iid, reader=lambda p: _data(**kw))
        return iid

    def _types(self, t):
        return list(read_events(self.conn, types=[t]))

    def test_payment_reminder_due_date_and_once(self):
        iid = self._invoice()
        first = acc.set_payment_reminder(self.conn, iid)
        again = acc.set_payment_reminder(self.conn, iid)
        self.assertIn(f"προς {Y['name']} για τις 14/12/2026", first["summary"])
        [r] = self._types("payment.reminder_set")
        self.assertEqual(r.payload["due_on"], "2026-12-14")  # 30 credit days
        self.assertEqual(r.payload["amount_cents"], 5823)
        self.assertEqual(again["event_ids"], [])

    def test_credit_needs_approval_and_describes(self):
        from xangx.master import tools as catalog
        catalog.load_role_tools()
        t = catalog.TOOLS["request_credit"]
        self.assertTrue(t.needs_approval)
        self.assertEqual(t.approval_action, "request_credit")
        iid = self._invoice()
        d = acc.describe_credit(self.conn, iid)
        self.assertIn("2,43 €", d["summary"])  # 2,15 + 13% VAT
        self.assertIn("Διανομές Υμηττού", d["summary"])
        # The stage Telegram (0:55-1:40): "προς" fits every supplier name, "στον Διανομές" is wrong Greek.
        self.assertIn(f"προς {Y['name']}", d["summary"])
        self.assertNotIn("στον", d["summary"])
        self.assertIn("Νερό", d["why"])

    def test_request_credit_writes_outbox_and_event_once(self):
        iid = self._invoice()
        out = acc.request_credit(self.conn, iid)
        acc.request_credit(self.conn, iid)
        [c] = self._types("credit.requested")
        self.assertEqual(c.payload["amount_cents"], 243)
        files = list((Path(self.tmp) / "outbox").glob("*.*"))
        self.assertEqual(len(files), 1)
        self.assertIn(Y["email"], files[0].read_bytes().decode("utf-8", "ignore"))
        self.assertIn("εξερχόμενα", out["summary"])
        self.assertNotIn("data/", out["summary"])
        self.assertIn(f"προς {Y['name']}", out["summary"])
        self.assertNotIn("στον", out["summary"])
        self.assertIn("της 14/11/2026", acc.credit_details(self.conn, iid)["body"])

    def test_daily_report(self):
        today = date(2026, 11, 14)
        append_event(self.conn, type="sale.recorded", actor="system",
                     at="2026-11-14T10:00:00.000Z",
                     payload={"receipt_id": "R-1", "total_cents": 248, "payment": "card",
                              "lines": [{"product_id": "cola-330", "qty": 2, "gross_cents": 248}]})
        self._invoice()
        out = acc.daily_report(self.conn, today)
        [r] = self._types("report.daily_created")
        self.assertEqual(r.payload["sales_cents"], 248)
        self.assertEqual(r.payload["purchases_cents"], 5823)
        self.assertEqual(r.payload["gross_profit_cents"], 200 - 114)  # net 2,00 - cost 2x0,57
        self.assertIn("όχι συμβουλή λογιστή", out["summary"])

    def test_worker_flags_iban_arithmetic_duplicate(self):
        self._invoice(iban=make_iban("00000099000000000000999"), total_delta=500)
        inbox = Path(self.tmp) / "inbox"
        (inbox / "b.pdf").write_bytes(b"other channel")
        [b] = inv.scan_inbox(self.conn)
        inv.process_invoice(self.conn, b, reader=lambda p: _data(total_delta=500))
        worker.run_once(self.conn)
        worker.run_once(self.conn)
        kinds = sorted(e.payload["kind"] for e in self._types("exception.flagged"))
        self.assertEqual(kinds, ["arithmetic", "duplicate_invoice", "new_iban"])
        [dup] = [e for e in self._types("exception.flagged")
                 if e.payload["kind"] == "duplicate_invoice"]
        self.assertIn("Α-2271", dup.payload["note"])
        self.assertNotIn("INV-", dup.payload["note"])

    def test_credit_reminder_uses_the_papers_due_date(self):
        self._invoice(payment={"terms": "credit", "credit_days": None, "due_date": "2026-12-01"})
        worker.run_once(self.conn)
        [r] = self._types("payment.reminder_set")
        self.assertEqual(r.payload["due_on"], "2026-12-01")
        self.assertEqual(self._types("payment.asked"), [])

    def test_credit_reminder_uses_the_papers_days(self):
        self._invoice(payment={"terms": "credit", "credit_days": 10, "due_date": None})
        worker.run_once(self.conn)
        [r] = self._types("payment.reminder_set")
        self.assertEqual(r.payload["due_on"], "2026-11-24")  # not the supplier's 30

    def test_unknown_terms_use_supplier_days(self):
        self._invoice()
        worker.run_once(self.conn)
        [r] = self._types("payment.reminder_set")
        self.assertEqual(r.payload["due_on"], "2026-12-14")
        self.assertEqual(self._types("payment.asked"), [])

    def test_cash_invoice_asks_once(self):
        self._invoice(payment={"terms": "cash", "credit_days": None, "due_date": None})
        worker.run_once(self.conn)
        worker.run_once(self.conn)
        [q] = self._types("payment.asked")
        self.assertEqual(q.payload["amount_cents"], 5823)
        self.assertEqual(q.payload["invoice_date"], "2026-11-14")
        self.assertEqual(q.payload["supplier_id"], "sup-ymittou")
        self.assertEqual(self._types("payment.reminder_set"), [])
        again = acc.ask_payment(self.conn, q.payload["invoice_id"])
        self.assertEqual(again["event_ids"], [])
        self.assertEqual(len(self._types("payment.asked")), 1)
        # "προς Διανομές Υμηττού Α.Ε." and one full stop, not "στον" or "Α.Ε..".
        self.assertIn(f"προς {Y['name']}", again["summary"])
        self.assertNotIn("στον", again["summary"])
        self.assertNotIn("..", again["summary"])

    def test_unknown_terms_and_no_credit_at_supplier_asks(self):
        with self.conn:
            self.conn.execute("UPDATE suppliers SET credit_days = 0 WHERE id = 'sup-ymittou'")
        self._invoice()
        worker.run_once(self.conn)
        self.assertEqual(len(self._types("payment.asked")), 1)
        self.assertEqual(self._types("payment.reminder_set"), [])


# ---------- purchase trends (GET /accountant/trends/purchases) ----------

K = SUPPLIER_BY_ID["sup-kava"]
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)  # Sunday 15/11/2026, 12:00 Greek time


def _cola_invoice(conn, invoice_id, supplier, day, cola_cents, qty=24):
    net = cola_cents * qty
    vat = round(net * 0.24)
    event, _ = append_event(
        conn, type="invoice.read", actor="system", subject=f"invoice:{invoice_id}",
        payload={"invoice_id": invoice_id, "supplier_afm": supplier["afm"], "series": "Α",
                 "number": invoice_id[-3:], "date": day, "net_cents": net,
                 "vat_by_rate": {"24": vat}, "total_cents": net + vat,
                 "checks": {"arithmetic": True}, "confidence": 0.9,
                 "lines": [{"name": "Coca-Cola 330ml κουτί", "product_id": "cola-330", "qty": qty,
                            "unit_cents": cola_cents, "net_cents": net, "vat_rate": 24}]})
    return event


class PurchaseTrends(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)
        seed_reference(self.conn)

    def tearDown(self):
        self.conn.close()

    def trends(self, days=30, until_id=None):
        return tr.purchase_trends(self.conn, days, until_id, now=NOW)

    def total(self, event):
        return event.payload["total_cents"]

    def test_price_change_and_cheapest(self):
        _cola_invoice(self.conn, "INV-k01", K, "2026-11-01", 58)
        _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        [cola] = self.trends()["prices"]
        self.assertEqual(cola["product_id"], "cola-330")
        self.assertEqual([p["unit_cents"] for p in cola["points"]], [58, 57, 62])
        self.assertEqual([p["supplier_id"] for p in cola["points"]],
                         ["sup-kava", "sup-ymittou", "sup-ymittou"])
        self.assertEqual((cola["last_cents"], cola["last_supplier_id"]), (62, "sup-ymittou"))
        # Against the same supplier's previous price (0,57), not the Kava's 0,58.
        self.assertEqual((cola["previous_cents"], cola["change_pct"]), (57, 8.8))
        self.assertEqual(cola["cheapest"], {"supplier_id": "sup-kava", "name": K["name"],
                                            "unit_cents": 58})
        # 72 cans in the 30 days, each 0,04 cheaper at the Kava.
        self.assertEqual((cola["qty_in_window"], cola["saving_cents"]), (72, 288))

    def test_changes_was_now_pct(self):
        _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        [change] = self.trends()["changes"]
        self.assertEqual((change["was_cents"], change["now_cents"], change["pct"]), (57, 62, 8.8))
        self.assertEqual((change["supplier_id"], change["date"]), ("sup-ymittou", "2026-11-14"))

    def test_same_price_is_not_a_change(self):
        _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 57)
        data = self.trends()
        self.assertEqual(data["prices"][0]["change_pct"], 0.0)
        self.assertEqual(data["changes"], [])

    def test_cheapest_is_the_same_as_compare_suppliers(self):
        from xangx.orders.tools import compare_suppliers

        _cola_invoice(self.conn, "INV-k01", K, "2026-11-01", 58)
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        [cola] = self.trends()["prices"]
        self.assertEqual(cola["cheapest"]["supplier_id"],
                         compare_suppliers(self.conn, "cola-330")["cheapest_supplier_id"])

    def test_no_saving_when_last_supplier_is_cheapest(self):
        _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 62)
        _cola_invoice(self.conn, "INV-k01", K, "2026-11-14", 58)
        [cola] = self.trends()["prices"]
        self.assertEqual(cola["last_supplier_id"], "sup-kava")
        self.assertEqual(cola["saving_cents"], 0)

    def test_reread_invoice_counts_once(self):
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 60)
        again = _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)  # the latest read wins
        data = self.trends()
        self.assertEqual(data["purchases"]["invoices"], 1)
        self.assertEqual(data["purchases"]["total_cents"], self.total(again))
        self.assertEqual([p["unit_cents"] for p in data["prices"][0]["points"]], [62])
        self.assertIsNone(data["prices"][0]["change_pct"])

    def test_purchases_per_supplier_in_window(self):
        y1 = _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        y2 = _cola_invoice(self.conn, "INV-y02", Y, "2026-11-15", 62, qty=48)
        k1 = _cola_invoice(self.conn, "INV-k01", K, "2026-11-10", 58)
        k0 = _cola_invoice(self.conn, "INV-k00", K, "2026-11-08", 58)  # before 09/11
        b = self.trends(days=7)["purchases"]
        rows = {r["supplier_id"]: r for r in b["suppliers"]}
        self.assertEqual(set(rows), {"sup-ymittou", "sup-kava"})
        self.assertEqual((rows["sup-ymittou"]["total_cents"], rows["sup-ymittou"]["invoices"]),
                         (self.total(y2), 1))
        self.assertEqual((rows["sup-kava"]["total_cents"], rows["sup-kava"]["invoices"]),
                         (self.total(k1), 1))
        self.assertEqual(b["suppliers"][0]["supplier_id"], "sup-ymittou")  # biggest first
        self.assertEqual(b["total_cents"], self.total(y2) + self.total(k1))
        self.assertEqual(rows["sup-ymittou"]["share_pct"],
                         round(self.total(y2) * 100.0 / b["total_cents"], 1))
        self.assertEqual(self.trends(days=30)["purchases"]["total_cents"],
                         sum(self.total(e) for e in (y1, y2, k1, k0)))

    def test_window_follows_the_greek_day(self):
        # 15/11 00:30 Greek time is still 14/11 22:30 UTC: the window already ends on 15/11.
        late = datetime(2026, 11, 14, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(tr.window(1, late), ("2026-11-15", "2026-11-15"))
        self.assertEqual(tr.window(7, NOW), ("2026-11-09", "2026-11-15"))

    def test_until_id_replays_the_past(self):
        first = _cola_invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        data = tr.purchase_trends(self.conn, 30, until_id=first.id, now=NOW)
        [cola] = data["prices"]
        self.assertEqual([p["unit_cents"] for p in cola["points"]], [57])
        self.assertEqual(data["changes"], [])
        self.assertEqual(data["purchases"]["invoices"], 1)
        self.assertIsNone(cola["cheapest"])  # no price from the future
        self.assertEqual(data["until_id"], first.id)

    def test_days_are_clamped(self):
        self.assertEqual(tr.clamp_days(0), 1)
        self.assertEqual(tr.clamp_days(10_000), tr.MAX_DAYS)
        self.assertEqual(tr.clamp_days("x"), tr.DEFAULT_DAYS)

    def test_reads_only(self):
        _cola_invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        before = len(read_events(self.conn))
        data = self.trends()
        self.assertEqual(len(read_events(self.conn)), before)
        self.assertEqual((data["from"], data["to"]), ("2026-10-17", "2026-11-15"))


class PurchaseTrendsRoute(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"),
            "XANGX_DATA_DIR": self.dir.name, "XANGX_FAKE_LLM": "1", "XANGX_ACCESS_CODE": ""})
        self.env.start()
        conn = connect()
        init_db(conn)
        seed_reference(conn)
        self.first = _cola_invoice(conn, "INV-k01", K, "2026-11-01", 58)
        _cola_invoice(conn, "INV-y02", Y, "2026-11-14", 62)
        conn.close()
        from xangx.server import app as server_app
        self.client = TestClient(server_app.app)

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_json_shape(self):
        r = self.client.get("/accountant/trends/purchases?days=7")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(set(data), {"days", "from", "to", "until_id", "prices", "changes",
                                     "purchases"})
        self.assertEqual(data["days"], 7)
        self.assertEqual(data["prices"][0]["cheapest"]["supplier_id"], "sup-kava")

    def test_until_id(self):
        r = self.client.get(f"/accountant/trends/purchases?until_id={self.first.id}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([p["unit_cents"] for p in r.json()["prices"][0]["points"]], [58])

    def test_old_route_is_gone(self):
        self.assertEqual(self.client.get("/accountant/trends").status_code, 404)


if __name__ == "__main__":
    unittest.main()
