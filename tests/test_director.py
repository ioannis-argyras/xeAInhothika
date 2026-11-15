import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xangx.demo import director
from xangx.demo.routes import router
from xangx.demo.seed import seed_all
from xangx.server import access
from xangx.server import app as server_app
from xangx.shared import connect, init_db, read_events
from xangx.shared.views import stock_levels
from xangx.stock import worker as stock_worker

CODE = "test-1234"

if not any(getattr(r, "path", "") == "/demo/steps" for r in server_app.app.routes):
    server_app.app.include_router(router)


class DirectorBase(unittest.TestCase):
    """A private data folder and database per test. The real data/ is never touched."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.data = root / "data"
        (self.data / "inbox").mkdir(parents=True)
        (self.data / "outbox").mkdir()
        self.db = str(self.data / "xangx.sqlite")
        self.env = mock.patch.dict(os.environ, {
            "XANGX_DB": self.db, "XANGX_DATA_DIR": str(self.data),
            "XANGX_FILES": str(self.data / "files"), "XANGX_FAKE_LLM": "1",
        })
        self.env.start()
        os.environ.pop("XANGX_ACCESS_CODE", None)
        self.conn = connect(self.db)
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def types(self, name):
        return read_events(self.conn, types=[name])


class ResetTests(DirectorBase):
    def test_reset_rebuilds_the_morning_and_keeps_the_files(self):
        (self.data / "inbox" / "a.pdf").write_bytes(b"%PDF-1 a")
        (self.data / "outbox" / "m.eml").write_text("mail", encoding="utf-8")
        seed_all(self.conn)
        fresh_cola = stock_levels(self.conn)["cola-330"]
        old_max = self.conn.execute("SELECT MAX(id) FROM events").fetchone()[0]
        director.reset_demo(self.conn)  # second build on top of an existing one
        result = director.reset_demo(self.conn)
        self.assertEqual(len(self.types("check.morning_done")), 1)
        self.assertEqual(stock_levels(self.conn)["cola-330"], fresh_cola)
        self.assertEqual(result["moved_files"], 0)
        self.assertEqual(list((self.data / "inbox").iterdir()), [])
        moved = sorted(p.name for p in (self.data / "_prin-reset").rglob("*") if p.is_file())
        self.assertEqual(moved, ["a.pdf", "m.eml"])
        self.assertGreater(self.conn.execute("SELECT MIN(id) FROM events").fetchone()[0], old_max)
        self.assertEqual(result["events"], self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertLess(result["ms"], 10000)

    def test_reset_moves_the_cameras_last_photo_aside_so_the_next_one_is_first(self):
        from xangx.stock import camera
        ref = camera.save_photo(b"\x89PNG\r\n\x1a\n" + b"rehearsal")
        camera.remember_photo("cam-1", ref)
        self.assertEqual(camera.last_photo("cam-1"), ref)
        result = director.reset_demo(self.conn)
        self.assertIsNone(camera.last_photo("cam-1"))
        self.assertEqual(result["moved_files"], 1)
        moved = sorted(p.name for p in (self.data / "_prin-reset").rglob("*") if p.is_file())
        self.assertEqual(moved, ["cam-1.last"])
        self.assertEqual(list((self.data / "camera").iterdir()), [])

    def test_reset_puts_the_append_only_triggers_back(self):
        director.reset_demo(self.conn)
        with self.assertRaises(Exception):
            with self.conn:
                self.conn.execute("DELETE FROM events")
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_workers_start_after_the_history(self):
        director.reset_demo(self.conn)
        morning = self.types("check.morning_done")[0]
        rows = {r["actor"]: r["last_id"] for r in self.conn.execute("SELECT * FROM cursors")}
        self.assertIn("stock", rows)
        # All workers start at the same place: the end of the seeded history. Only what the morning
        # check itself wrote (its notices for the owner, then check.morning_done) comes after it,
        # so the notifier still delivers those and nothing older is replayed.
        self.assertEqual(len(set(rows.values())), 1)
        start = next(iter(rows.values()))
        self.assertLess(start, morning.id)
        after = read_events(self.conn, after_id=start)
        self.assertEqual(after[-1].id, morning.id)
        self.assertTrue(all(e.actor == "master" and e.type in ("notification.sent", "check.morning_done") for e in after))


class SeedScriptTests(DirectorBase):
    def test_seed_script_starts_the_workers_at_the_end_of_the_history(self):
        self.conn.close()
        script = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"
        run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, XANGX_DB=self.db, PYTHONIOENCODING="utf-8"))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.conn = connect(self.db)
        last = self.conn.execute("SELECT MAX(id) FROM events").fetchone()[0]
        self.assertGreater(last, 1000)
        rows = {r["actor"]: r["last_id"] for r in self.conn.execute("SELECT * FROM cursors")}
        for actor in director.cursor_actors([]):
            self.assertEqual(rows.get(actor), last, actor)


class StepTests(DirectorBase):
    def setUp(self):
        super().setUp()
        director.reset_demo(self.conn)

    def make_pdfs(self, names):
        folder = self.data / "demo_invoices"
        folder.mkdir(exist_ok=True)
        for n in names:
            (folder / n).write_bytes(b"%PDF-1.4 " + n.encode())

    def test_kamera_writes_taken_and_the_worker_flags_low(self):
        out = director.run_step("kamera", self.conn)
        self.assertTrue(out["ok"], out)
        taken = self.types("shelf.items_taken")
        self.assertEqual((taken[0].payload["product_id"], taken[0].payload["taken"]), ("cola-330", 5))
        stock_worker.run_once(self.conn)
        self.assertEqual(self.types("stock.low_detected")[0].payload["on_hand"], 3)

    def test_kamera_twice_does_not_break(self):
        self.assertTrue(director.run_step("kamera", self.conn)["ok"])
        self.assertTrue(director.run_step("kamera", self.conn)["ok"])

    def test_email_without_the_pdfs_says_what_to_run(self):
        out = director.run_step("email", self.conn)
        self.assertFalse(out["ok"])
        self.assertIn("make_demo_invoices.py --pdf", out["summary"])
        self.assertEqual(list((self.data / "inbox").iterdir()), [])

    def test_email_and_timologio_copy_the_right_pdfs(self):
        self.make_pdfs(director.INVOICE_FILES.values())
        self.assertTrue(director.run_step("email", self.conn)["ok"])
        self.assertEqual(sorted(p.name for p in (self.data / "inbox").iterdir()),
                         ["B-512-artopoieio.pdf", "G-318-galakto.pdf"])
        self.assertTrue(director.run_step("timologio", self.conn)["ok"])
        self.assertEqual(len(list((self.data / "inbox").iterdir())), 3)
        self.assertEqual(len(list((self.data / "demo_invoices").iterdir())), 3)  # copies, not moves

    def test_chat_steps_use_the_owner_message_path(self):
        out = director.run_step("entoli", self.conn, background=False)
        self.assertTrue(out["ok"], out)
        said = self.types("owner.said")
        self.assertEqual(said[0].payload["text"], "Τακτοποίησε τις εκκρεμότητες.")
        self.assertTrue(director.run_step("entoli2", self.conn, background=False)["ok"])
        self.assertEqual(len(self.types("owner.said")), 2)

    def test_paralavi_closes_only_with_exactly_one_water_missing(self):
        out = director.run_step("paralavi", self.conn)
        self.assertTrue(out["ok"], out)
        done = self.types("delivery.completed")
        self.assertEqual(len(done), 1)
        self.assertEqual([(s["product_id"], s["qty"]) for s in done[0].payload["shortages"]],
                         [("water-1500x6", 1)])
        scans = [e for e in self.types("delivery.item_scanned")
                 if e.payload["product_id"] == "water-1500x6"]
        self.assertEqual(len(scans), 9)

    def test_paralavi_twice_scans_no_more_water(self):
        director.run_step("paralavi", self.conn)
        again = director.run_step("paralavi", self.conn)
        self.assertTrue(again["ok"])
        self.assertIn("έχει ήδη κλείσει", again["summary"])
        scans = [e for e in self.types("delivery.item_scanned")
                 if e.payload["product_id"] == "water-1500x6"]
        self.assertEqual(len(scans), 9)

    def test_a_step_never_raises(self):
        self.conn.execute("DELETE FROM positions")
        self.conn.commit()
        out = director.run_step("kamera", self.conn)
        self.assertFalse(out["ok"])
        self.assertIn("summary", out)


class RouteTests(DirectorBase):
    def setUp(self):
        super().setUp()
        access.reset_attempts()
        self.client = TestClient(server_app.app, follow_redirects=False)

    def tearDown(self):
        access.reset_attempts()
        super().tearDown()

    def test_page_and_steps(self):
        page = self.client.get("/demo/skinothetis")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Σκηνοθέτης", page.text)
        steps = self.client.get("/demo/steps").json()
        self.assertEqual([s["key"] for s in steps], list(director.STEP_KEYS))
        self.assertEqual(len(steps), 6)

    def test_unknown_step_is_404(self):
        self.assertEqual(self.client.post("/demo/step/nope").status_code, 404)
        self.assertEqual(self.client.post("/demo/step/entoli3").status_code, 404)

    def test_reset_and_a_step_over_http(self):
        reset = self.client.post("/demo/reset").json()
        self.assertTrue(reset["ok"], reset)
        self.assertLess(reset["ms"], 10000)
        step = self.client.post("/demo/step/kamera").json()
        self.assertTrue(step["ok"], step)

    def test_all_routes_need_the_access_code(self):
        os.environ["XANGX_ACCESS_CODE"] = CODE
        for method, path in (("get", "/demo/skinothetis"), ("get", "/demo/steps"),
                             ("post", "/demo/reset"), ("post", "/demo/step/kamera")):
            r = getattr(self.client, method)(path)
            self.assertIn(r.status_code, (302, 303, 401), (path, r.status_code))
        # nothing happened behind the closed door
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.client.post("/login", data={"code": CODE})
        self.assertEqual(self.client.get("/demo/steps").status_code, 200)


if __name__ == "__main__":
    unittest.main()
