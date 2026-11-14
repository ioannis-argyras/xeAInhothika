"""Trends for the owner: sales per Greek day, prices per product and supplier, purchases."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi.testclient import TestClient

from xangx.accountant import trends as tr
from xangx.demo.catalog_data import SUPPLIER_BY_ID
from xangx.demo.seed import seed_reference
from xangx.shared import append_event, connect, init_db, read_events

Y, K = SUPPLIER_BY_ID["sup-ymittou"], SUPPLIER_BY_ID["sup-kava"]
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)  # Sunday 15/11/2026, 12:00 Greek time


def sale(conn, at, cents, receipt):
    append_event(conn, type="sale.recorded", actor="system", at=at, idem_key=f"sale:{receipt}",
                 payload={"receipt_id": receipt, "total_cents": cents, "payment": "card",
                          "lines": [{"product_id": "cola-330", "qty": 1, "gross_cents": cents}]})


def invoice(conn, invoice_id, supplier, day, cola_cents, qty=24, at=None):
    net = cola_cents * qty
    vat = round(net * 0.24)
    append_event(conn, type="invoice.read", actor="system", at=at, subject=f"invoice:{invoice_id}",
                 payload={"invoice_id": invoice_id, "supplier_afm": supplier["afm"], "series": "Α",
                          "number": invoice_id[-3:], "date": day, "net_cents": net,
                          "vat_by_rate": {"24": vat}, "total_cents": net + vat,
                          "checks": {"arithmetic": True}, "confidence": 0.9,
                          "lines": [{"name": "Coca-Cola 330ml κουτί", "product_id": "cola-330",
                                     "qty": qty, "unit_cents": cola_cents, "net_cents": net,
                                     "vat_rate": 24}]})
    return net + vat


class Trends(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)
        seed_reference(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_greek_day_around_midnight(self):
        # November: Greece is UTC+2. 21:59:59 UTC is still 14/11, 22:00:00 UTC is already 15/11.
        sale(self.conn, "2026-11-14T21:59:59.000Z", 300, "R-1")
        sale(self.conn, "2026-11-14T22:00:00.000Z", 500, "R-2")
        sale(self.conn, "2026-11-13T22:00:00.000Z", 200, "R-3")  # 00:00 Greek time on 14/11
        days = {d["date"]: d for d in tr.sales_by_day(self.conn, 7, NOW)["days"]}
        self.assertEqual((days["2026-11-14"]["total_cents"], days["2026-11-14"]["receipts"]), (500, 2))
        self.assertEqual((days["2026-11-15"]["total_cents"], days["2026-11-15"]["receipts"]), (500, 1))
        self.assertEqual(days["2026-11-13"]["total_cents"], 0)
        self.assertTrue(days["2026-11-15"]["today"])
        self.assertEqual(days["2026-11-15"]["weekday"], "Κυριακή")

    def test_summer_time_day(self):
        # July: UTC+3. 20:59 UTC is 23:59 Greek time of the same day.
        self.assertEqual(tr.greek_date("2026-07-10T20:59:00.000Z").isoformat(), "2026-07-10")
        self.assertEqual(tr.greek_date("2026-07-10T21:00:00.000Z").isoformat(), "2026-07-11")

    def test_average_best_day_and_same_weekday(self):
        sale(self.conn, "2026-11-07T10:00:00.000Z", 1000, "R-1")  # Saturday a week before
        sale(self.conn, "2026-11-14T10:00:00.000Z", 1500, "R-2")  # Saturday
        sale(self.conn, "2026-11-13T10:00:00.000Z", 700, "R-3")   # Friday
        sale(self.conn, "2026-11-15T08:00:00.000Z", 9999, "R-4")  # today: not over yet
        s = tr.sales_by_day(self.conn, 7, NOW)
        self.assertEqual(s["total_without_today_cents"], 1500 + 700)
        self.assertEqual(len(s["days"]), 7)
        self.assertEqual(s["days"][0]["date"], "2026-11-09")
        self.assertEqual(s["days_with_sales"], 2)
        self.assertEqual(s["average_cents"], 1100)  # (1500 + 700) / 2, without today
        self.assertEqual(s["best_day"]["date"], "2026-11-14")
        sat = next(d for d in s["days"] if d["date"] == "2026-11-14")
        self.assertEqual(sat["same_weekday_last_week_cents"], 1000)  # outside the 7 days, still found
        self.assertEqual(sat["vs_last_week_pct"], 50.0)
        fri = next(d for d in s["days"] if d["date"] == "2026-11-13")
        self.assertIsNone(fri["vs_last_week_pct"])  # nothing to compare with
        self.assertEqual(s["total_cents"], 1500 + 700 + 9999)

    def test_no_sales(self):
        s = tr.sales_by_day(self.conn, 30, NOW)
        self.assertEqual((s["average_cents"], s["best_day"], s["total_cents"]), (None, None, 0))

    def test_price_change_and_cheapest(self):
        invoice(self.conn, "INV-k01", K, "2026-11-01", 58)
        invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        [cola] = tr.prices(self.conn, days=30, now=NOW)
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

    def test_no_saving_when_last_supplier_is_cheapest(self):
        invoice(self.conn, "INV-y01", Y, "2026-11-07", 62)
        invoice(self.conn, "INV-k01", K, "2026-11-14", 58)
        [cola] = tr.prices(self.conn, days=30, now=NOW)
        self.assertEqual(cola["last_supplier_id"], "sup-kava")
        self.assertEqual(cola["saving_cents"], 0)

    def test_weekday_average(self):
        for k, (day, cents) in enumerate([("2026-10-31", 1000), ("2026-11-07", 1000),
                                          ("2026-11-14", 1020)]):  # three Saturdays
            sale(self.conn, f"{day}T10:00:00.000Z", cents, f"R-{k}")
        s = tr.sales_by_day(self.conn, 30, NOW)
        sat = next(w for w in s["by_weekday"] if w["weekday"] == "Σάββατο")
        self.assertEqual((sat["days"], sat["average_cents"], sat["last_date"]), (3, 1007, "2026-11-14"))
        self.assertEqual(sat["last_vs_average_pct"], 1.3)
        mon = s["by_weekday"][0]
        self.assertEqual((mon["weekday"], mon["days"], mon["average_cents"]), ("Δευτέρα", 0, None))

    def test_cheapest_is_the_same_as_compare_suppliers(self):
        from xangx.orders.tools import compare_suppliers

        invoice(self.conn, "INV-k01", K, "2026-11-01", 58)
        invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        [cola] = tr.prices(self.conn, now=NOW)
        self.assertEqual(cola["cheapest"]["supplier_id"],
                         compare_suppliers(self.conn, "cola-330")["cheapest_supplier_id"])

    def test_reread_invoice_counts_once(self):
        invoice(self.conn, "INV-y02", Y, "2026-11-14", 60)
        total = invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)  # read again: the latest wins
        b = tr.purchases(self.conn, 30, NOW)
        self.assertEqual(b["invoices"], 1)
        self.assertEqual(b["total_cents"], total)
        [cola] = tr.prices(self.conn, now=NOW)
        self.assertEqual([p["unit_cents"] for p in cola["points"]], [62])
        self.assertIsNone(cola["change_pct"])

    def test_purchases_per_supplier_in_window(self):
        y1 = invoice(self.conn, "INV-y01", Y, "2026-11-07", 57)
        y2 = invoice(self.conn, "INV-y02", Y, "2026-11-15", 62, qty=48)
        k1 = invoice(self.conn, "INV-k01", K, "2026-11-10", 58)
        invoice(self.conn, "INV-k00", K, "2026-11-08", 58)  # outside 7 days (09/11 to 15/11)
        b = tr.purchases(self.conn, 7, NOW)
        rows = {r["supplier_id"]: r for r in b["suppliers"]}
        self.assertEqual(set(rows), {"sup-ymittou", "sup-kava"})
        self.assertEqual((rows["sup-ymittou"]["total_cents"], rows["sup-ymittou"]["invoices"]),
                         (y2, 1))
        self.assertEqual((rows["sup-kava"]["total_cents"], rows["sup-kava"]["invoices"]), (k1, 1))
        self.assertEqual(b["suppliers"][0]["supplier_id"], "sup-ymittou")  # biggest first
        self.assertEqual(b["total_cents"], y2 + k1)
        self.assertEqual(rows["sup-ymittou"]["share_pct"], round(y2 * 100.0 / (y2 + k1), 1))
        self.assertAlmostEqual(sum(r["share_pct"] for r in b["suppliers"]), 100.0, delta=0.11)
        self.assertEqual(tr.purchases(self.conn, 30, NOW)["total_cents"], y1 + y2 + k1 + k1)

    def test_days_are_clamped(self):
        self.assertEqual(tr.clamp_days(0), 1)
        self.assertEqual(tr.clamp_days(10_000), tr.MAX_DAYS)
        self.assertEqual(tr.clamp_days("x"), tr.DEFAULT_DAYS)

    def test_reads_only(self):
        sale(self.conn, "2026-11-14T10:00:00.000Z", 1500, "R-1")
        invoice(self.conn, "INV-y02", Y, "2026-11-14", 62)
        before = len(read_events(self.conn))
        data = tr.trends(self.conn, 30, NOW)
        self.assertEqual(len(read_events(self.conn)), before)
        self.assertEqual((data["from"], data["to"]), ("2026-10-17", "2026-11-15"))
        text = tr.summary_text(data)
        self.assertIn("Τελευταίες 30 μέρες", text)
        self.assertIn("14/11/2026", text)


class TrendsRoute(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"),
            "XANGX_DATA_DIR": self.dir.name, "XANGX_FAKE_LLM": "1", "XANGX_ACCESS_CODE": ""})
        self.env.start()
        conn = connect()
        init_db(conn)
        seed_reference(conn)
        invoice(conn, "INV-k01", K, "2026-11-01", 58)
        invoice(conn, "INV-y02", Y, "2026-11-14", 62)
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
        r = self.client.get("/accountant/trends?days=7")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["days"], 7)
        self.assertEqual(len(data["sales"]["days"]), 7)
        self.assertEqual(set(data), {"days", "from", "to", "sales", "prices", "purchases", "summary"})
        self.assertEqual(data["prices"][0]["cheapest"]["supplier_id"], "sup-kava")


if __name__ == "__main__":
    unittest.main()
