import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from xangx.accountant import core as acc
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
        acc.set_payment_reminder(self.conn, iid)
        again = acc.set_payment_reminder(self.conn, iid)
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

    def test_unknown_terms_and_no_credit_at_supplier_asks(self):
        with self.conn:
            self.conn.execute("UPDATE suppliers SET credit_days = 0 WHERE id = 'sup-ymittou'")
        self._invoice()
        worker.run_once(self.conn)
        self.assertEqual(len(self._types("payment.asked")), 1)
        self.assertEqual(self._types("payment.reminder_set"), [])


if __name__ == "__main__":
    unittest.main()
