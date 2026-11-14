import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from xangx.server import access
from xangx.server import app as server_app

CODE = "test-1234"


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "t.sqlite")
        self.env = mock.patch.dict(os.environ, {"XANGX_DB": self.db})
        self.env.start()
        os.environ.pop("XANGX_ACCESS_CODE", None)
        os.environ.pop("XANGX_SESSION_SECRET", None)
        access.reset_attempts()
        self.client = TestClient(server_app.app, follow_redirects=False)

    def tearDown(self):
        self.env.stop()
        access.reset_attempts()
        try:
            self.dir.cleanup()
        except OSError:
            pass

    def with_code(self):
        os.environ["XANGX_ACCESS_CODE"] = CODE

    def login(self, code=CODE, **kw):
        return self.client.post("/login", data={"code": code}, **kw)

    def test_no_code_means_no_login(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/state").status_code, 200)

    def test_healthz_without_cookie(self):
        self.with_code()
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})

    def test_page_redirects_and_api_gives_401(self):
        self.with_code()
        r = self.client.get("/", headers={"accept": "text/html"})
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))
        r = self.client.get("/api/state")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"detail": "Χρειάζεται κωδικός ομάδας."})
        r = self.client.get("/api/events/stream", headers={"accept": "text/event-stream"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/pause")
        self.assertEqual(r.status_code, 401)

    def test_login_page(self):
        self.with_code()
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn('lang="el"', r.text)
        self.assertIn("Κωδικός ομάδας", r.text)
        self.assertNotIn("Λάθος κωδικός", r.text)

    def test_wrong_code_shows_error(self):
        self.with_code()
        r = self.login("nope")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Λάθος κωδικός", r.text)
        self.assertNotIn("set-cookie", r.headers)

    def test_right_code_sets_cookie_and_opens_api(self):
        self.with_code()
        r = self.login()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("xangx_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("max-age=604800", cookie)
        self.assertNotIn("secure", cookie)
        self.assertEqual(self.client.get("/api/state").status_code, 200)
        self.assertEqual(self.client.get("/", headers={"accept": "text/html"}).status_code, 200)

    def test_secure_flag_behind_https_tunnel(self):
        self.with_code()
        r = self.login(headers={"x-forwarded-proto": "https"})
        self.assertIn("secure", r.headers["set-cookie"].lower())

    def test_sixth_wrong_attempt_is_429(self):
        self.with_code()
        for _ in range(5):
            self.assertEqual(self.login("nope").status_code, 401)
        r = self.login("nope")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Πάρα πολλές προσπάθειες", r.text)
        # Locked even with the right code, until the window passes.
        self.assertEqual(self.login().status_code, 429)
        # Another client is not locked.
        self.assertEqual(self.login(headers={"cf-connecting-ip": "203.0.113.9"}).status_code, 303)

    def test_spoofed_forwarded_for_does_not_dodge_the_limit(self):
        self.with_code()
        for i in range(5):
            r = self.login("nope", headers={"x-forwarded-for": f"198.51.100.{i}"})
            self.assertEqual(r.status_code, 401)
        r = self.login(headers={"x-forwarded-for": "198.51.100.99"})
        self.assertEqual(r.status_code, 429)

    def test_each_cf_connecting_ip_counts_separately(self):
        self.with_code()
        for _ in range(5):
            r = self.login("nope", headers={"cf-connecting-ip": "203.0.113.1"})
            self.assertEqual(r.status_code, 401)
        self.assertEqual(self.login(headers={"cf-connecting-ip": "203.0.113.1"}).status_code, 429)
        self.assertEqual(self.login(headers={"cf-connecting-ip": "203.0.113.2"}).status_code, 303)

    def test_link_login_sets_cookie_and_drops_code(self):
        self.with_code()
        r = self.client.get(f"/stock/camera?code={CODE}&x=1")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/stock/camera?x=1")
        self.assertIn("xangx_session=", r.headers["set-cookie"])
        r = self.client.get(f"/?code={CODE}")
        self.assertEqual(r.headers["location"], "/")
        self.assertNotIn("code", r.headers["location"])

    def test_link_login_wrong_code_counts_as_attempt(self):
        self.with_code()
        for _ in range(5):
            r = self.client.get("/?code=nope", headers={"accept": "text/html"})
            self.assertEqual(r.status_code, 303)
            self.assertTrue(r.headers["location"].startswith("/login"))
            self.assertNotIn("nope", r.headers["location"])
        self.assertEqual(self.client.get("/?code=nope").status_code, 429)

    def test_next_only_relative(self):
        self.with_code()
        for bad in ("https://evil.example/x", "//evil.example", "evil", "/\\evil.example"):
            r = self.client.post("/login", data={"code": CODE, "next": bad})
            self.assertEqual(r.headers["location"], "/", bad)
        r = self.client.post("/login", data={"code": CODE, "next": "/stock/camera?a=1"})
        self.assertEqual(r.headers["location"], "/stock/camera?a=1")

    def test_no_public_docs(self):
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        self.with_code()
        self.login()
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        self.assertEqual(self.client.get("/redoc").status_code, 404)

    def test_cookie_from_other_code_is_refused(self):
        self.with_code()
        self.login()
        os.environ["XANGX_ACCESS_CODE"] = "another-code"
        self.assertEqual(self.client.get("/api/state").status_code, 401)

    def test_explicit_session_secret(self):
        self.with_code()
        os.environ["XANGX_SESSION_SECRET"] = "s3cret-for-test"
        self.login()
        self.assertEqual(self.client.get("/api/state").status_code, 200)
        os.environ.pop("XANGX_SESSION_SECRET")
        self.assertEqual(self.client.get("/api/state").status_code, 401)


if __name__ == "__main__":
    unittest.main()
