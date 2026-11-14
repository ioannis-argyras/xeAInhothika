import importlib.util
import os
import sqlite3
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("test_server_script", os.path.join(ROOT, "scripts", "test_server.py"))
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


class BuildEnvTest(unittest.TestCase):
    def test_all_secrets_are_removed(self):
        fake = {
            "ANTHROPIC_API_KEY": "x", "XANGX_ACCESS_CODE": "x", "XANGX_SESSION_SECRET": "x",
            "XANGX_MCP_TOKEN": "x", "XANGX_DEMO_SUPPLIER_EMAIL": "a@b.invalid",
            "MYDATA_USER": "x", "MYDATA_AFM": "x", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_OWNER_CHAT_ID": "1",
            "SMTP_HOST": "x", "SMTP_PASSWORD": "x", "IMAP_HOST": "x", "IMAP_PASSWORD": "x",
            "PATH": "keep-me",
        }
        env = script.build_env(fake, 8090, "db.sqlite", "datadir")
        for name in fake:
            if name != "PATH":
                self.assertNotIn(name, env, name)
        self.assertEqual(env["PATH"], "keep-me")
        self.assertEqual(env["XANGX_FAKE_LLM"], "1")
        self.assertEqual(env["XANGX_PORT"], "8090")
        self.assertEqual(env["XANGX_DB"], "db.sqlite")
        self.assertEqual(env["XANGX_DATA_DIR"], "datadir")
        self.assertEqual(env["XANGX_FILES"], os.path.join("datadir", "files"))
        self.assertEqual(env["XANGX_AUTORUN"], "0")
        self.assertEqual(env["PYTHONPATH"], script.ROOT)

    def test_autorun_switch(self):
        self.assertEqual(script.build_env({}, 8090, "d", "x", autorun=True)["XANGX_AUTORUN"], "1")

    def test_base_environment_is_not_changed(self):
        base = {"ANTHROPIC_API_KEY": "x"}
        script.build_env(base, 8090, "d", "x")
        self.assertIn("ANTHROPIC_API_KEY", base)


class PortTest(unittest.TestCase):
    def test_demo_port_is_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            script.check_port(8010)
        self.assertIn("8010", str(ctx.exception))

    def test_other_port_is_fine(self):
        script.check_port(8090)

    def test_main_refuses_demo_port(self):
        with self.assertRaises(SystemExit):
            script.main(["--port", "8010"])


class CopyDbTest(unittest.TestCase):
    def test_backup_has_same_event_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.sqlite")
            dst = os.path.join(tmp, "dst.sqlite")
            conn = sqlite3.connect(src)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, type TEXT)")
            conn.executemany("INSERT INTO events (type) VALUES (?)", [("a",)] * 25)
            conn.commit()
            script.copy_db(src, dst)
            conn.close()
            copy = sqlite3.connect(dst)
            try:
                self.assertEqual(copy.execute("SELECT COUNT(*) FROM events").fetchone()[0], 25)
            finally:
                copy.close()


if __name__ == "__main__":
    unittest.main()
