import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "elegxos_klaseon.py"
WORK = REPO / ".tmp" / "elegxos-klaseon-test"


def run(*args):
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout


class ClassCheckTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        try:
            WORK.rmdir()
        except OSError:
            pass

    def write(self, name, css):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(css, encoding="utf-8")

    def test_same_bare_class_with_layout_in_two_files(self):
        self.write("a.css", ".mi{position:absolute}\n")
        self.write("b.css", ".mi{display:flex}\n")
        code, out = run("--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("ΣΥΓΚΡΟΥΣΗ .mi: a.css:1, b.css:1", out)

    def test_parent_selector_is_not_bare(self):
        self.write("a.css", ".a .mi{position:absolute}\n")
        self.write("b.css", ".mi{display:flex}\n")
        code, out = run("--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertNotIn("ΣΥΓΚΡΟΥΣΗ", out)

    def test_no_layout_property_is_fine(self):
        self.write("a.css", ".last{color:red}\n")
        self.write("b.css", ".last{color:red}\n")
        self.assertEqual(run("--root", str(self.root))[0], 0)

    def test_media_and_comma_pieces_count(self):
        self.write("a.css", "@media (max-width:600px){\n  .x,.y:hover{width:10px}\n}\n")
        self.write("b.css", "/* c */\n.y{height:5px}\n")
        code, out = run("--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("ΣΥΓΚΡΟΥΣΗ .y: a.css:2, b.css:2", out)
        self.assertNotIn(".x:", out)

    def test_vendor_is_skipped(self):
        self.write("a.css", ".v{display:flex}\n")
        self.write("vendor/b.css", ".v{display:flex}\n")
        self.assertEqual(run("--root", str(self.root))[0], 0)

    def test_unreadable_file_is_an_error_not_ok(self):
        (self.root / "bad.css").write_bytes(b"\xff\xfe\x00bad\x80")
        code, out = run("--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("ΣΦΑΛΜΑ: bad.css", out)
        self.assertNotIn("OK", out.splitlines())

    def test_current_repo_has_only_known_names(self):
        code, out = run()
        # Names found today that are not in the 15/11 audit stay visible (exit 1), see the report.
        for name in ("back", "tabs", "tog"):
            self.assertNotIn(f"ΣΥΓΚΡΟΥΣΗ .{name}:", out)

    def test_mutation_on_a_copy_of_the_repo(self):
        copy = self.root / "xangx"
        shutil.copytree(REPO / "xangx", copy, ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.js", "*.html", "*.png", "*.svg", "*.woff*"))
        files = [p for p in sorted(copy.rglob("*.css")) if "vendor" not in p.parts]
        first, second = files[0], files[1]
        for path in (first, second):
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n.last{position:absolute}\n")
        code, out = run("--root", str(copy))
        self.assertEqual(code, 1)
        self.assertIn("ΣΥΓΚΡΟΥΣΗ .last:", out)

    def test_one_added_file_alone_is_not_a_conflict(self):
        copy = self.root / "xangx"
        shutil.copytree(REPO / "xangx", copy, ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.js", "*.html", "*.png", "*.svg", "*.woff*"))
        first = [p for p in sorted(copy.rglob("*.css")) if "vendor" not in p.parts][0]
        with open(first, "a", encoding="utf-8") as f:
            f.write("\n.last{position:absolute}\n")
        code, out = run("--root", str(copy))
        self.assertNotIn("ΣΥΓΚΡΟΥΣΗ .last", out)


if __name__ == "__main__":
    unittest.main()
