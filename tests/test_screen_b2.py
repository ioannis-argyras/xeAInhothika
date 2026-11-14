import os
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xangx.demo.seed import seed_reference
from xangx.screen import api_b2
from xangx.shared.db import connect, init_db
from xangx.shared.events import append_event
from xangx.stock import camera, worker

JPG_A = b"\xff\xd8\xff" + b"A" * 40
JPG_B = b"\xff\xd8\xff" + b"B" * 40


def counts(taken_by_position):
    def fn(prev_path, new_path, positions):
        return [{"position_id": pid, "taken": n, "added": 0, "confidence": "high"}
                for pid, n in taken_by_position.items()]
    return fn


class ShelfApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.files = os.path.join(self.dir.name, "files")
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": os.path.join(self.dir.name, "t.sqlite"), "XANGX_FILES": self.files})
        self.env.start()
        conn = connect()
        init_db(conn)
        seed_reference(conn)
        self.conn = conn
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM positions WHERE camera_id='cam-1' ORDER BY id")}
        self.p1, self.p2 = list(rows)[0], list(rows)[1]
        self.prod1, self.prod2 = rows[self.p1]["product_id"], rows[self.p2]["product_id"]
        low = {r["id"]: r["low_threshold"] for r in conn.execute("SELECT id, low_threshold FROM products")}
        stock = {pid: t + 30 for pid, t in low.items()}
        stock[self.prod1] = low[self.prod1] + 2
        for pid, qty in stock.items():
            append_event(conn, type="stock.moved", actor="stock", subject=f"product:{pid}",
                         payload={"product_id": pid, "qty": qty, "reason": "delivery"})
        app = FastAPI()
        app.include_router(api_b2.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def photos(self, taken):
        camera.handle_photo(self.conn, "cam-1", JPG_A, count=counts({}))
        out = camera.handle_photo(self.conn, "cam-1", JPG_B, count=counts(taken))
        worker.run_once(self.conn)
        return out

    def test_empty_day_has_no_low_and_no_latest(self):
        data = self.client.get("/api/screen/shelves").json()
        self.assertEqual(data["low_count"], 0)
        self.assertIsNone(data["latest"])
        self.assertEqual(data["total"], len(data["positions"]))
        self.assertTrue(all(p["last"] is None for p in data["positions"]))

    def test_low_first_then_latest_move(self):
        self.photos({self.p1: 5, self.p2: 1})
        data = self.client.get("/api/screen/shelves").json()
        first, second = data["positions"][0], data["positions"][1]
        self.assertEqual(first["position_id"], self.p1)
        self.assertTrue(first["low"])
        self.assertEqual(first["taken_today"], 5)
        self.assertEqual(second["position_id"], self.p2)
        self.assertFalse(second["low"])
        self.assertEqual(data["low_count"], 1)
        self.assertEqual(data["camera_id"], "cam-1")
        self.assertIsNotNone(data["latest"])
        self.assertEqual(len(first["last"]["at"]), 5)
        # the moved positions come before the ones nobody touched
        moved = [p["position_id"] for p in data["positions"] if p["last"]]
        self.assertEqual(data["positions"][:2][0]["position_id"], moved[0])

    def test_photo_urls_and_serving(self):
        self.photos({self.p1: 5})
        latest = self.client.get("/api/screen/shelves").json()["latest"]
        self.assertTrue(latest["after_url"].startswith("/api/screen/photo/"))
        self.assertTrue(latest["before_url"].startswith("/api/screen/photo/"))
        r = self.client.get(latest["after_url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.content, JPG_B)

    def test_until_id_is_respected(self):
        self.photos({self.p1: 5})
        before_moves = self.conn.execute(
            "SELECT MAX(id) AS m FROM events WHERE type='stock.moved'").fetchone()["m"]
        data = self.client.get(f"/api/screen/shelves?until_id={before_moves}").json()
        self.assertEqual(data["low_count"], 0)
        self.assertIsNone(data["latest"])

    def test_photo_rejects_everything_but_a_stored_photo_name(self):
        self.photos({self.p1: 5})
        os.makedirs(self.files, exist_ok=True)
        with open(os.path.join(self.files, "secret.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(self.dir.name, "outside.jpg"), "wb") as f:
            f.write(JPG_A)
        good = "a" * 64
        for name in ("..%2Fx", "../x", "%2e%2e%2f" + good + ".jpg", "a" * 63 + ".jpg",
                     good + "0.jpg", good + ".txt", good + ".jpg%0A", "secret.txt",
                     good.upper() + ".jpg", good + ".jpg/..", "..%5Coutside.jpg"):
            self.assertEqual(self.client.get(f"/api/screen/photo/{name}").status_code, 404, name)
        # a well formed name whose file does not exist is 404 too
        self.assertEqual(self.client.get(f"/api/screen/photo/{good}.jpg").status_code, 404)


if __name__ == "__main__":
    unittest.main()
