"""Photo of a paper invoice: POST /invoices/photo, the status route, and the access code."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xangx.server import app as server_app
from xangx.shared import append_event, connect, init_db, read_events

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"jpeg-test-bytes"


class PhotoTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"),
            "XANGX_DATA_DIR": self.dir.name, "XANGX_FAKE_LLM": "1", "XANGX_ACCESS_CODE": ""})
        self.env.start()
        self.client = TestClient(server_app.app)

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_jpeg_is_accepted_and_registered(self):
        r = self.client.post("/invoices/photo", content=JPEG, headers={"content-type": "image/jpeg"})
        self.assertEqual(r.status_code, 200, r.text)
        invoice_id = r.json()["invoice_id"]
        self.assertTrue(invoice_id.startswith("INV-"))
        conn = connect()
        init_db(conn)
        events = read_events(conn, types=["invoice.received"])
        conn.close()
        self.assertEqual([e.payload["invoice_id"] for e in events], [invoice_id])
        self.assertEqual(events[0].payload["source"], "photo")
        inbox = Path(self.dir.name) / "inbox"
        self.assertEqual([p for p in inbox.iterdir() if p.suffix != ""] if inbox.exists() else [], [])
        self.assertTrue((Path(self.dir.name) / events[0].payload["file_ref"]).is_file())

    def test_same_photo_twice_gives_same_invoice(self):
        a = self.client.post("/invoices/photo", content=JPEG).json()["invoice_id"]
        b = self.client.post("/invoices/photo", content=JPEG).json()["invoice_id"]
        self.assertEqual(a, b)

    def test_wrong_bytes_are_refused(self):
        r = self.client.post("/invoices/photo", content=b"MZ not an invoice at all")
        self.assertEqual(r.status_code, 415)
        self.assertIn("JPG", r.json()["detail"])

    def test_too_big_is_refused(self):
        r = self.client.post("/invoices/photo", content=JPEG + b"\x00" * (10 * 1024 * 1024))
        self.assertEqual(r.status_code, 413)

    def test_empty_body_is_refused(self):
        self.assertEqual(self.client.post("/invoices/photo", content=b"").status_code, 400)

    def test_status_of_unread_invoice(self):
        invoice_id = self.client.post("/invoices/photo", content=JPEG).json()["invoice_id"]
        s = self.client.get("/invoices/photo/status", params={"invoice_id": invoice_id}).json()
        self.assertFalse(s["done"])
        self.assertIsNone(s["read"])

    def test_status_lists_what_the_invoice_says(self):
        invoice_id = self.client.post("/invoices/photo", content=JPEG).json()["invoice_id"]
        conn = connect()
        init_db(conn)
        append_event(conn, type="invoice.read", actor="system", subject=invoice_id, payload={
            "invoice_id": invoice_id, "series": "A", "number": "2271", "supplier_afm": "111111111",
            "total_cents": 4886, "net_cents": 3638, "date": "2026-11-14", "vat_by_rate": {"24": 357, "13": 280},
            "confidence": 0.9, "checks": {"sums": True},
            "lines": [
                {"name": "Coca-Cola 330ml κουτί", "qty": 24, "unit_cents": 62, "net_cents": 1488, "vat_rate": 24},
                {"name": "Νερό 1,5L εξάδα", "qty": 10, "unit_cents": 215, "net_cents": 2150, "vat_rate": 13}]})
        conn.close()
        s = self.client.get("/invoices/photo/status", params={"invoice_id": invoice_id}).json()
        self.assertEqual(s["read"]["lines"], 2)
        self.assertEqual(len(s["read"]["items"]), 2)
        self.assertEqual(s["read"]["items"][0], {
            "name": "Coca-Cola 330ml κουτί", "qty": 24, "unit": "0,62 €", "net": "14,88 €"})
        self.assertEqual(s["read"]["items"][1]["net"], "21,50 €")

    def test_status_unknown_and_bad_id(self):
        self.assertEqual(self.client.get("/invoices/photo/status", params={"invoice_id": "INV-00000000"}).status_code, 404)
        self.assertEqual(self.client.get("/invoices/photo/status", params={"invoice_id": "a b;"}).status_code, 400)

    def test_page_and_assets(self):
        page = self.client.get("/invoices/photo")
        self.assertEqual(page.status_code, 200)
        self.assertIn('lang="el"', page.text)
        self.assertIn('capture="environment"', page.text)
        self.assertEqual(self.client.get("/invoices/photo/assets/photo.js").status_code, 200)
        self.assertEqual(self.client.get("/invoices/photo/assets/photo.css").status_code, 200)
        self.assertEqual(self.client.get("/invoices/photo/assets/other.js").status_code, 404)


class PhotoAccessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"),
            "XANGX_ACCESS_CODE": "test-1234", "XANGX_DATA_DIR": self.dir.name})
        self.env.start()
        self.client = TestClient(server_app.app, follow_redirects=False)

    def tearDown(self):
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def test_blocked_without_the_code(self):
        r = self.client.get("/invoices/photo")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.post("/invoices/photo", content=JPEG).status_code, 401)
        self.assertEqual(self.client.get("/invoices/photo/status", params={"invoice_id": "INV-1"}).status_code, 401)

    def test_served_after_login(self):
        self.assertEqual(self.client.post("/login", data={"code": "test-1234"}).status_code, 303)
        self.assertEqual(self.client.get("/invoices/photo").status_code, 200)


if __name__ == "__main__":
    unittest.main()
