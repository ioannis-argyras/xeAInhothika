"""The month's folder: totals, CSV for Excel in Greek, the ZIP, and the access code."""

import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from tests.test_accountant import _data
from xangx.accountant import core as acc
from xangx.accountant import month as month_mod
from xangx.accountant.tools import monthly_folder
from xangx.demo.seed import seed_reference
from xangx.invoices import core as inv
from xangx.server import access
from xangx.server import app as server_app
from xangx.shared import append_event, connect, init_db, read_events


class MonthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.tmp, "t.sqlite"), "XANGX_DATA_DIR": self.tmp,
            "XANGX_FAKE_LLM": "1", "XANGX_ACCESS_CODE": "", "MYDATA_USER_ID": "",
            "MYDATA_SUBSCRIPTION_KEY": ""})
        self.env.start()
        access.reset_attempts()
        self.conn = connect()
        init_db(self.conn)
        seed_reference(self.conn)
        self.ids = []
        for number, content in (("2271", b"first invoice"), ("2272", b"second invoice")):
            inbox = Path(self.tmp) / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / "a.pdf").write_bytes(content)
            [iid] = inv.scan_inbox(self.conn)
            inv.process_invoice(self.conn, iid, reader=lambda p, n=number: _data(number=n))
            self.ids.append(iid)
        acc.set_payment_reminder(self.conn, self.ids[0])
        append_event(self.conn, type="payment.recorded", actor="owner",
                     payload={"invoice_id": self.ids[1], "method": "card", "amount_cents": 5823})
        # an invoice from another month must stay out
        inbox = Path(self.tmp) / "inbox"
        (inbox / "b.pdf").write_bytes(b"october invoice")
        [other] = inv.scan_inbox(self.conn)
        data = _data(number="1999")
        data["date"] = "2026-10-30"
        inv.process_invoice(self.conn, other, reader=lambda p: data)
        self.client = TestClient(server_app.app)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_totals_and_summary(self):
        data = month_mod.collect(self.conn, "2026-11")
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total_cents"], 2 * 5823)
        self.assertEqual(data["vat_total"], {"24": 2 * 657, "13": 2 * 280})
        self.assertEqual(data["unpaid"], 1)
        text = month_mod.summary_text(data)
        self.assertTrue(text.startswith("Νοέμβριος 2026: 2 τιμολόγια, 116,46 €, ΦΠΑ 18,74 €, 1 εκκρεμεί πληρωμή"), text)
        pays = sorted(i["payment"] for i in data["items"])
        self.assertTrue(pays[0].startswith("Πληρώθηκε με κάρτα"))
        self.assertTrue(pays[1].startswith("Υπενθύμιση, λήξη"))

    def test_tool_writes_event(self):
        r = monthly_folder(self.conn, month="2026-11")
        self.assertIn("2 τιμολόγια", r["summary"])
        [e] = read_events(self.conn, types=["report.month_created"])
        self.assertEqual(e.payload["invoice_count"], 2)
        self.assertEqual(e.payload["month"], "2026-11")
        self.assertTrue((Path(self.tmp) / e.payload["file_ref"]).is_file())
        again = monthly_folder(self.conn, month="2026-11")
        self.assertEqual(again["event_ids"], [])
        self.assertIn("ΕΕΕΕ-ΜΜ", monthly_folder(self.conn, month="11/2026")["summary"])

    def test_csv_has_bom_semicolon_and_comma_amounts(self):
        raw = month_mod.make_csv(month_mod.collect(self.conn, "2026-11"))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        lines = raw.decode("utf-8-sig").splitlines()
        self.assertEqual(len(lines), 4)  # header, 2 invoices, total
        self.assertIn(";", lines[0])
        self.assertIn("58,23", lines[1])
        self.assertIn("ΦΠΑ 24%", lines[0])
        self.assertTrue(lines[3].startswith("Σύνολο;"))

    def test_route_gives_zip_with_files(self):
        r = self.client.get("/accountant/month?month=2026-11")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        self.assertIn("synopsi.csv", names)
        self.assertIn("synopsi.html", names)
        invoice_files = [n for n in names if n.startswith("timologia/")]
        self.assertEqual(len(invoice_files), 2)
        self.assertEqual(sorted(z.read(n) for n in invoice_files), [b"first invoice", b"second invoice"])
        self.assertTrue(z.read("synopsi.csv").startswith(b"\xef\xbb\xbf"))
        self.assertIn("lang=\"el\"", z.read("synopsi.html").decode("utf-8"))
        # same bytes the second time, one file
        self.assertEqual(self.client.get("/accountant/month?month=2026-11").content, r.content)
        self.assertEqual(self.client.get("/accountant/month?month=nope").status_code, 400)

    def test_without_code_it_is_401(self):
        with mock.patch.dict(os.environ, {"XANGX_ACCESS_CODE": "kodikos"}):
            r = self.client.get("/accountant/month?month=2026-11")
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json(), {"detail": "Χρειάζεται κωδικός ομάδας."})


if __name__ == "__main__":
    unittest.main()
