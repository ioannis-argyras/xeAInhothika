import base64
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from xangx.demo.seed import seed_all
from xangx.server import app as server_app
from xangx.shared import views
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event, read_events
from xangx.stock import camera, worker


def jpeg(tag):
    return b"\xff\xd8\xff\xe0" + tag.encode() * 64


def b64(data):
    return base64.b64encode(data).decode()


class CameraTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory()
        cls.seed_db = os.path.join(cls.seed_dir.name, "seed.sqlite")
        conn = connect(cls.seed_db)
        init_db(conn)
        seed_all(conn)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.seed_dir.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        src = connect(self.seed_db)
        dst = connect(self.db)
        src.backup(dst)
        src.close()
        dst.close()
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": self.db,
            "XANGX_FILES": os.path.join(self.dir.name, "files"),
            "XANGX_FAKE_LLM": "1",
        })
        self.env.start()
        self.client = TestClient(server_app.app)
        self.conn = connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def post(self, data, cam="cam-1"):
        return self.client.post("/stock/camera/photo", json={"camera_id": cam, "image_b64": b64(data)})

    def shelf_events(self):
        return read_events(self.conn, types=["shelf.items_taken"])

    def test_page_is_served(self):
        r = self.client.get("/stock/camera?cam=cam-1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Φωτογραφία τώρα", r.text)

    def first_position(self):
        """The fridge's first row, from the data (Coca-Cola before 15/11, cold brew after)."""
        return camera.camera_positions(self.conn, "cam-1")[0]

    def test_two_photos_give_fake_taken(self):
        r1 = self.post(jpeg("a"))
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["first"])
        self.assertEqual(self.shelf_events(), [])

        r2 = self.post(jpeg("b"))
        self.assertEqual(r2.status_code, 200)
        results = r2.json()["results"]
        first = self.first_position()
        self.assertEqual(results[0]["taken"], camera.FAKE_TAKEN)
        self.assertEqual(results[0]["position_name"], first["name"])
        events = self.shelf_events()
        self.assertEqual(len(events), 1)
        p = events[0].payload
        self.assertEqual((p["position_id"], p["product_id"], p["taken"]), (first["position_id"], first["product_id"], camera.FAKE_TAKEN))
        self.assertEqual(events[0].actor, "camera")
        self.assertTrue(os.path.exists(os.path.join(os.environ["XANGX_FILES"], p["file_ref"])))

    def test_practice_counts_but_writes_nothing(self):
        self.post(jpeg("real"))  # the real camera's last photo
        practice = lambda data: self.client.post("/stock/camera/photo", json={
            "camera_id": "cam-1", "image_b64": b64(data), "practice": True})
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertTrue(practice(jpeg("a")).json()["first"])  # practice keeps its own chain
        r = practice(jpeg("b")).json()
        self.assertEqual((r["practice"], r["results"][0]["taken"], r["event_ids"]), (True, camera.FAKE_TAKEN, []))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
        self.assertEqual(camera.last_photo("cam-1"), camera.save_photo(jpeg("real")))
        self.assertEqual(self.post(jpeg("c")).json()["results"][0]["taken"], camera.FAKE_TAKEN)  # real chain still works
        self.assertEqual(len(self.shelf_events()), 1)

    def test_no_internet_gives_plain_greek_502(self):
        import anthropic

        class Down:  # a client whose every call fails like a cut network
            class beta:
                class messages:
                    @staticmethod
                    def create(**kw):
                        raise anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)

        a = camera.save_photo(jpeg("a"))
        b = camera.save_photo(jpeg("b"))
        files = camera.files_dir()
        with self.assertRaises(camera.CameraError) as cm:
            camera.count_taken(files / a, files / b, [{"position_id": "p", "name": "n", "product_name": "x"}],
                               client=Down)
        self.assertEqual(str(cm.exception), camera.NO_NET)
        self.post(jpeg("a"))
        with mock.patch.object(camera, "count_taken", side_effect=camera.CameraError(camera.NO_NET)):
            r = self.post(jpeg("b"))
        self.assertEqual((r.status_code, r.json()["detail"]), (502, camera.NO_NET))
        self.assertEqual(camera.last_photo("cam-1"), camera.save_photo(jpeg("a")))  # the old photo stays

    def test_same_photo_again_writes_nothing(self):
        self.post(jpeg("a"))
        self.post(jpeg("b"))
        before = len(self.shelf_events())
        r = self.post(jpeg("b"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [])
        self.assertEqual(len(self.shelf_events()), before)

    def test_resent_photo_after_another_is_not_counted_twice(self):
        self.post(jpeg("a"))
        self.post(jpeg("b"))
        conn = connect(self.db)
        try:
            camera.remember_photo("cam-1", camera.save_photo(jpeg("a")))
            camera.handle_photo(conn, "cam-1", jpeg("b"))
        finally:
            conn.close()
        self.assertEqual(len(self.shelf_events()), 1)

    def test_bad_requests_give_400(self):
        self.assertEqual(self.client.post("/stock/camera/photo", json={"camera_id": "cam-1"}).status_code, 400)
        self.assertEqual(self.client.post("/stock/camera/photo",
                                          json={"camera_id": "cam-1", "image_b64": "%%%"}).status_code, 400)
        self.assertEqual(self.post(b"GIF89a not a photo").status_code, 400)
        self.assertEqual(self.post(jpeg("a"), cam="cam-404").status_code, 400)
        big = b"\xff\xd8\xff\xe0" + b"0" * (5 * 1024 * 1024)
        self.assertEqual(self.post(big).status_code, 400)

    def test_low_detected_once_a_day(self):
        pid = self.first_position()["product_id"]
        on_hand = views.stock_on_hand(self.conn, pid)
        self.post(jpeg("a"))
        self.post(jpeg("b"))
        worker.run_once(self.conn)
        low = read_events(self.conn, types=["stock.low_detected"])
        self.assertEqual(len(low), 1)
        p = low[0].payload
        self.assertEqual(p["product_id"], pid)
        self.assertEqual(p["on_hand"], on_hand - camera.FAKE_TAKEN)
        self.assertEqual(p["source"], "camera")
        self.assertGreater(p["per_day"], 0)
        self.assertEqual(low[0].caused_by, self.shelf_events()[0].id)

        self.post(jpeg("c"))
        worker.run_once(self.conn)
        self.assertEqual(len(read_events(self.conn, types=["stock.low_detected"])), 1)


class CountTakenTest(unittest.TestCase):
    POS = [{"position_id": "p1", "name": "Ράφι", "product_name": "Cola"},
           {"position_id": "p2", "name": "Ράφι 2", "product_name": "Fanta"}]

    def test_clean_drops_bad_answers(self):
        raw = [
            {"position_id": "p1", "taken": 3, "added": 0, "confidence": "high"},
            {"position_id": "p1", "taken": 9, "added": 0, "confidence": "high"},   # duplicate
            {"position_id": "zz", "taken": 1, "added": 0, "confidence": "high"},   # unknown
            {"position_id": "p2", "taken": 99, "added": 0, "confidence": "high"},  # too many
            {"position_id": "p2", "taken": True, "added": 0, "confidence": "low"},  # not a number
        ]
        self.assertEqual(camera._clean(raw, self.POS),
                         [{"position_id": "p1", "taken": 3, "added": 0, "confidence": "high"}])

    def test_fake_without_key(self):
        with mock.patch.dict(os.environ, {"XANGX_FAKE_LLM": "1"}):
            self.assertEqual(camera.count_taken("a", "b", self.POS)[0]["taken"], camera.FAKE_TAKEN)



class DeliveryTest(unittest.TestCase):
    """Delivery check with barcodes, on the demo data plus a small sent order of 10 water packs."""

    setUpClass = classmethod(CameraTest.setUpClass.__func__)
    tearDownClass = classmethod(CameraTest.tearDownClass.__func__)
    setUp = CameraTest.setUp
    tearDown = CameraTest.tearDown

    def order(self, order_id="P-0900", qty=10):
        append_event(self.conn, type="order.proposed", actor="orders", subject=f"order:{order_id}",
                     payload={"order_id": order_id, "supplier_id": "sup-ymittou", "net_cents": qty * 215,
                              "lines": [{"product_id": "water-1500x6", "qty": qty, "unit_cents": 215,
                                         "last_unit_cents": 215}],
                              "why": {"per_day": 1.0, "on_hand": 2, "lead_days": 1}})
        return self.client.post("/stock/delivery/start", json={"order_id": order_id}).json()

    def barcode(self, pid):
        return self.conn.execute("SELECT barcode FROM products WHERE id = ?", (pid,)).fetchone()[0]

    def scan(self, code, delivery_id="D-P-0900"):
        return self.client.post("/stock/delivery/scan", json={"delivery_id": delivery_id, "barcode": code})

    def test_error_texts_show_the_order_not_inner_codes(self):
        # List row #18: the owner reads "P-0318", never "D-P-0318" or "cam-1".
        self.order("P-0905", 10)
        self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0905"})
        late = self.scan(self.barcode("water-1500x6"), "D-P-0905")
        self.assertEqual((late.status_code, late.json()["detail"]),
                         (400, "Η παραλαβή της P-0905 έχει ήδη κλείσει."))
        self.assertEqual(self.scan("5449000000996", "D-P-0999").json()["detail"],
                         "Δεν έχει ξεκινήσει η παραλαβή της P-0999.")
        self.assertEqual(self.scan("5449000000996", "<script>").json()["detail"],
                         "Δεν έχει ξεκινήσει η παραλαβή.")
        photo = self.client.post("/stock/camera/photo", json={"camera_id": "cam-404", "image_b64": b64(jpeg("a"))})
        self.assertNotIn("cam-404", photo.json()["detail"])
        shelf = self.client.get("/stock/camera/shelf?cam=cam-404")
        self.assertEqual(shelf.status_code, 404)
        self.assertNotIn("cam-404", shelf.json()["detail"])

    def test_page_and_decoder_are_served(self):
        r = self.client.get("/stock/scan?order=P-0318")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Με το χέρι", r.text)
        w = self.client.get("/stock/vendor/zxing_reader.wasm")
        self.assertEqual(w.headers["content-type"], "application/wasm")
        self.assertEqual(self.client.get("/stock/vendor/../routes.py").status_code, 404)

    def test_case_barcode_counts_pack_size(self):
        append_event(self.conn, type="order.proposed", actor="orders", subject="order:P-0901",
                     payload={"order_id": "P-0901", "supplier_id": "sup-ymittou", "net_cents": 48 * 57,
                              "lines": [{"product_id": "cola-330", "qty": 48, "unit_cents": 57,
                                         "last_unit_cents": 57}],
                              "why": {"per_day": 6.0, "on_hand": 8, "lead_days": 1}})
        self.client.post("/stock/delivery/start", json={"order_id": "P-0901"})
        case = self.conn.execute("SELECT case_barcode, pack_size FROM supplier_products WHERE "
                                 "supplier_id = 'sup-ymittou' AND product_id = 'cola-330'").fetchone()
        self.assertEqual(case[1], 24)
        r = self.scan(case[0], "D-P-0901").json()
        self.assertEqual((r["scanned"], r["expected"], r["last"]["units"]), (24, 48, 24))
        r = self.scan(self.barcode("cola-330"), "D-P-0901").json()
        self.assertEqual((r["scanned"], r["last"]["units"]), (25, 1))
        r = self.scan(case[0], "D-P-0901").json()
        self.assertEqual((r["scanned"], r["missing"]), (48, []))
        before = views.stock_on_hand(self.conn, "cola-330")
        self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0901"})
        self.assertEqual(views.stock_on_hand(self.conn, "cola-330") - before, 49)

    def test_practice_counts_but_writes_nothing(self):
        self.order("P-0903", 10)
        water = self.barcode("water-1500x6")
        self.client.post("/stock/delivery/scan", json={"delivery_id": "D-P-0903", "barcode": water})
        self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0903"})
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        r = self.client.post("/stock/delivery/practice",
                             json={"order_id": "P-0903", "barcodes": [water, water, "123456789"]})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual((j["practice"], j["completed"], j["scanned"], j["expected"]), (True, False, 2, 10))
        self.assertEqual(j["unknown_barcodes"], ["123456789"])
        self.assertIsNone(j["last"]["product_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
        bad = self.client.post("/stock/delivery/practice", json={"order_id": "P-0903", "barcodes": ["12"]})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post("/stock/delivery/practice",
                                          json={"order_id": "P-0903", "barcodes": "x"}).status_code, 400)

    def test_supplier_value_of_missing_and_pending_order(self):
        s = self.order("P-0904", 10)
        self.assertTrue(s["supplier_name"])
        r = self.scan(self.barcode("water-1500x6"), "D-P-0904").json()
        self.assertEqual(r["missing"][0]["value_cents"], 9 * 215)
        self.assertEqual(self.client.get("/stock/camera/shelf?cam=cam-1").json()["name"], "Ψυγείο 1")
        self.assertEqual(self.client.get("/stock/camera/shelf?cam=nope").status_code, 404)
        from xangx.stock import delivery
        sent = delivery.pending_order(self.conn)
        self.assertEqual(self.client.get("/stock/delivery/pending").json()["order_id"], sent)
        if sent:
            delivery.start(self.conn, sent)
            delivery.complete(self.conn, delivery.delivery_id_for(sent))
            self.assertNotEqual(delivery.pending_order(self.conn), sent)

    def test_case_barcode_of_another_supplier_is_unknown(self):
        sup = self.conn.execute("SELECT id FROM suppliers WHERE id != 'sup-ymittou' LIMIT 1").fetchone()[0]
        append_event(self.conn, type="order.proposed", actor="orders", subject="order:P-0902",
                     payload={"order_id": "P-0902", "supplier_id": sup, "net_cents": 57,
                              "lines": [{"product_id": "cola-330", "qty": 24, "unit_cents": 57,
                                         "last_unit_cents": 57}],
                              "why": {"per_day": 6.0, "on_hand": 8, "lead_days": 1}})
        self.client.post("/stock/delivery/start", json={"order_id": "P-0902"})
        case = self.conn.execute("SELECT case_barcode FROM supplier_products WHERE "
                                 "supplier_id = 'sup-ymittou' AND product_id = 'cola-330'").fetchone()[0]
        r = self.scan(case, "D-P-0902").json()
        self.assertIsNone(r["last"]["product_id"])
        self.assertEqual((r["scanned"], r["unknown_barcodes"]), (0, [case]))

    def test_nine_of_ten_gives_a_shortage_of_one(self):
        s = self.order()
        self.assertEqual((s["scanned"], s["expected"]), (0, 10))
        water = self.barcode("water-1500x6")
        for _ in range(9):
            r = self.scan(water)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["scanned"], r.json()["expected"]), (9, 10))
        self.assertEqual(r.json()["missing"][0]["qty"], 1)
        before = views.stock_on_hand(self.conn, "water-1500x6")
        done = self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0900"}).json()
        self.assertTrue(done["completed"])
        p = read_events(self.conn, types=["delivery.completed"])[-1].payload
        self.assertEqual(p["shortages"], [{"product_id": "water-1500x6", "qty": 1}])
        self.assertEqual(p["lines"], [{"product_id": "water-1500x6", "expected": 10, "scanned": 9}])
        self.assertEqual(views.stock_on_hand(self.conn, "water-1500x6"), before + 9)
        moved = [e for e in read_events(self.conn, types=["stock.moved"])
                 if e.payload["reason"] == "delivery" and e.caused_by is not None
                 and e.payload["product_id"] == "water-1500x6"]
        self.assertEqual(moved[-1].payload["unit_cost_cents"], 215)
        self.assertEqual(self.scan(water).status_code, 400)  # closed: no more scans

    def test_unknown_barcode_is_kept(self):
        self.order()
        r = self.scan("0000000000017").json()
        self.assertEqual(r["unknown_barcodes"], ["0000000000017"])
        self.assertIsNone(r["last"]["product_id"])
        self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0900"})
        p = read_events(self.conn, types=["delivery.completed"])[-1].payload
        self.assertEqual(p["unknown_barcodes"], ["0000000000017"])
        self.assertEqual(self.scan("12ab").status_code, 400)

    def test_complete_twice_writes_once(self):
        self.order()
        self.scan(self.barcode("water-1500x6"))
        self.scan(self.barcode("cola-330"))  # not ordered: comes as extra
        for _ in range(2):
            r = self.client.post("/stock/delivery/complete", json={"delivery_id": "D-P-0900"})
            self.assertEqual(r.status_code, 200)
        done = [e for e in read_events(self.conn, types=["delivery.completed"])
                if e.payload["delivery_id"] == "D-P-0900"]
        self.assertEqual(len(done), 1)
        moved = [e for e in read_events(self.conn, types=["stock.moved"])
                 if (e.idem_key or "").startswith("delivery:D-P-0900:")]
        self.assertEqual(sorted(e.payload["product_id"] for e in moved), ["cola-330", "water-1500x6"])
        self.assertEqual(r.json()["extra"][0]["product_id"], "cola-330")
        self.client.post("/stock/delivery/start", json={"order_id": "P-0900"})  # again: no 2nd start
        self.assertEqual(len([e for e in read_events(self.conn, types=["delivery.started"])
                              if e.payload["delivery_id"] == "D-P-0900"]), 1)

    def test_unknown_order_and_tool(self):
        r = self.client.post("/stock/delivery/start", json={"order_id": "P-9999"})
        self.assertEqual(r.status_code, 400)
        from xangx.stock import tools
        self.assertIn("Δεν έχει ξεκινήσει", tools.delivery_status(self.conn, "P-0318")["summary"])
        self.order()
        self.scan(self.barcode("water-1500x6"))
        out = tools.delivery_status(self.conn, "P-0900")
        self.assertIn("1 από 10", out["summary"])
        self.assertIn("9 Νερό", out["summary"])


if __name__ == "__main__":
    unittest.main()


class TrendsTest(unittest.TestCase):
    """Sales trends on a small database of our own: known receipts at known Greek times."""

    def setUp(self):
        from datetime import datetime, timezone
        self.dir = tempfile.TemporaryDirectory()
        self.conn = connect(os.path.join(self.dir.name, "t.sqlite"))
        init_db(self.conn)
        self.conn.executemany("INSERT INTO products (id, name, vat_rate, sell_price_cents) VALUES (?,?,?,?)",
                              [("cola", "Coca-Cola", 24, 100), ("water", "Νερό", 13, 50)])
        self.conn.commit()
        self.utc = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def sale(self, n, at, lines):
        total = sum(q * p for _, q, p in lines)
        return append_event(self.conn, type="sale.recorded", actor="system", at=at,
                            payload={"receipt_id": f"R-{n}", "total_cents": total, "payment": "card",
                                     "lines": [{"product_id": pid, "qty": q, "gross_cents": q * p}
                                               for pid, q, p in lines]})[0]

    def test_days_hours_top_and_until_id(self):
        from xangx.stock import trends
        # 14/11 22:30 UTC = 15/11 00:30 Greek time (winter, +2): counts on 15/11, hour 0.
        self.sale(1, "2026-11-14T22:30:00.000Z", [("cola", 2, 100)])
        self.sale(2, "2026-11-15T08:10:00.000Z", [("water", 3, 50), ("cola", 1, 100)])
        third = self.sale(3, "2026-11-15T08:40:00.000Z", [("water", 4, 50)])
        self.sale(4, "2026-10-01T08:00:00.000Z", [("cola", 9, 100)])  # outside 30 days
        late = self.sale(5, "2026-11-15T09:00:00.000Z", [("cola", 5, 100)])

        t = trends.sales_trends(self.conn, until_id=third.id, days=30)
        self.assertEqual((t["from"], t["to"], t["days"], t["until_id"]), ("17/10/2026", "15/11/2026", 30, third.id))
        self.assertEqual(len(t["per_day"]), 30)
        self.assertEqual(t["per_day"][-1], {"date": "15/11/2026", "total_cents": 650, "receipts": 3})
        self.assertEqual(t["per_day"][-2], {"date": "14/11/2026", "total_cents": 0, "receipts": 0})
        self.assertEqual(len(t["per_hour"]), 24)
        self.assertEqual(t["per_hour"][0], {"hour": 0, "avg_total_cents": round(200 / 30), "avg_receipts": 0.0})
        self.assertEqual(t["per_hour"][10]["avg_total_cents"], round(450 / 30))  # 08:10 and 08:40 UTC = 10:xx
        self.assertEqual(t["top_products"], [
            {"product_id": "water", "name": "Νερό", "qty": 7, "total_cents": 350},
            {"product_id": "cola", "name": "Coca-Cola", "qty": 3, "total_cents": 300}])

        t = trends.sales_trends(self.conn, days=30)  # without until_id: the clock decides
        self.assertIn(late.id, [t["until_id"]])

    def test_summer_time_and_route(self):
        from xangx.stock import trends
        # 15/07 21:30 UTC = 16/07 00:30 Greek summer time (+3).
        e = self.sale(1, "2026-07-15T21:30:00.000Z", [("cola", 1, 100)])
        t = trends.sales_trends(self.conn, until_id=e.id, days=1)
        self.assertEqual((t["from"], t["per_day"][0]["receipts"], t["per_hour"][0]["avg_total_cents"]),
                         ("16/07/2026", 1, 100))
        with mock.patch.dict(os.environ, {"XANGX_DB": os.path.join(self.dir.name, "t.sqlite")}):
            client = TestClient(server_app.app)
            client.cookies.set("xangx_access", "")
            with mock.patch.dict(os.environ, {"XANGX_ACCESS_CODE": ""}):
                r = client.get(f"/stock/trends/sales?until_id={e.id}&days=1")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["top_products"][0]["qty"], 1)
                self.assertEqual(client.get("/stock/trends/sales?days=0").status_code, 400)
