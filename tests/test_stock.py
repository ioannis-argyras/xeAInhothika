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
from xangx.shared.events import read_events
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

    def test_two_photos_give_taken_5(self):
        r1 = self.post(jpeg("a"))
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["first"])
        self.assertEqual(self.shelf_events(), [])

        r2 = self.post(jpeg("b"))
        self.assertEqual(r2.status_code, 200)
        results = r2.json()["results"]
        self.assertEqual(results[0]["taken"], 5)
        self.assertEqual(results[0]["position_name"], "Ψυγείο 1 · 1η σειρά")
        events = self.shelf_events()
        self.assertEqual(len(events), 1)
        p = events[0].payload
        self.assertEqual((p["position_id"], p["product_id"], p["taken"]), ("fridge1-r1", "cola-330", 5))
        self.assertEqual(events[0].actor, "camera")
        self.assertTrue(os.path.exists(os.path.join(os.environ["XANGX_FILES"], p["file_ref"])))

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
        on_hand = views.stock_on_hand(self.conn, "cola-330")
        self.post(jpeg("a"))
        self.post(jpeg("b"))
        worker.run_once(self.conn)
        low = read_events(self.conn, types=["stock.low_detected"])
        self.assertEqual(len(low), 1)
        p = low[0].payload
        self.assertEqual(p["product_id"], "cola-330")
        self.assertEqual(p["on_hand"], on_hand - 5)
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
            self.assertEqual(camera.count_taken("a", "b", self.POS)[0]["taken"], 5)


if __name__ == "__main__":
    unittest.main()
