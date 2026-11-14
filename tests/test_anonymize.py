import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from xangx.demo.catalog_data import afm_valid, iban_valid, make_afm, make_iban
from xangx.demo.seed import seed_reference
from xangx.shared import connect, init_db, read_events

_spec = importlib.util.spec_from_file_location(
    "anonymize", Path(__file__).parent.parent / "tools" / "anonymize" / "anonymize.py")
anon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anon)

# Invented "real" data for the test only.
REAL_AFM = make_afm("12345678")
REAL_IBAN = make_iban("01100000000001234567890")
CUSTOMER_AFM = make_afm("87654321")


def fake_reader(path):
    n = int(path.stem[-1])
    return {
        "supplier_name": "Παπαδόπουλος Τρόφιμα ΟΕ" if n < 3 else "Κάβα Δοκιμής ΕΠΕ",
        "supplier_afm": REAL_AFM if n < 3 else make_afm("23456789"),
        "customer_name": "Μίνι Μάρκετ Δοκιμής", "customer_afm": CUSTOMER_AFM,
        "series": "Α", "number": str(100 + n), "date": f"2026-10-0{n}",
        "lines": [{"name": "Coca-Cola 330ml κουτί Παπαδόπουλος", "qty": 24, "unit_cents": 55 + n,
                   "net_cents": 24 * (55 + n), "vat_rate": 24}],
        "net_cents": 24 * (55 + n), "vat_by_rate": {"24": round(24 * (55 + n) * 0.24)},
        "total_cents": 24 * (55 + n) + round(24 * (55 + n) * 0.24),
        "iban": REAL_IBAN if n < 3 else None,
        "pages": {"seen": 1, "total": 1}, "hidden_instructions": None,
    }


class Anonymize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        real = self.tmp / "pragmatika"
        real.mkdir()
        for n in range(1, 5):
            (real / f"inv{n}.jpg").write_bytes(f"image {n}".encode())
        self.saved = {k: getattr(anon, k) for k in ("REAL", "CACHE", "OUT", "BLOCKLIST", "ROOT")}
        anon.ROOT = self.tmp
        anon.REAL = real
        anon.CACHE = real / ".cache"
        anon.OUT = real / "anon" / "history.json"
        anon.BLOCKLIST = self.tmp / "blocklist.local.txt"
        self.calls = 0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(anon, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        def reader(p):
            self.calls += 1
            return fake_reader(p)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = anon.run(reader=reader)
        return code, buf.getvalue()

    def test_no_real_values_left_and_prices_kept(self):
        code, printed = self._run()
        self.assertEqual(code, 0)
        text = anon.OUT.read_text(encoding="utf-8")
        for real in (REAL_AFM, REAL_IBAN, CUSTOMER_AFM, "Παπαδόπουλος", "Κάβα Δοκιμής", "Μίνι Μάρκετ"):
            self.assertNotIn(real, text)
            self.assertNotIn(real, printed)
        data = json.loads(text)
        self.assertEqual([s["name"] for s in data["suppliers"]], ["Προμηθευτής Α", "Προμηθευτής Β"])
        for s in data["suppliers"]:
            self.assertTrue(afm_valid(s["afm"]))
            self.assertTrue(iban_valid(s["iban"]))
        first = data["invoices"][0]
        self.assertEqual((first["date"], first["lines"][0]["unit_cents"]), ("2026-10-01", 56))

    def test_blocklist_gets_terms_once_and_they_match(self):
        import re
        self._run()
        self._run()
        lines = anon.BLOCKLIST.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(set(lines)))
        joined = "\n".join(lines)
        for real in ("Παπαδόπουλος Τρόφιμα ΟΕ", REAL_AFM, CUSTOMER_AFM,
                     " ".join(REAL_IBAN[i:i + 4] for i in range(0, len(REAL_IBAN), 4))):
            self.assertTrue(any(re.search(t, real, re.IGNORECASE) for t in lines), real)
        self.assertNotIn(REAL_AFM + "x", joined)

    def test_each_file_read_once(self):
        self._run()
        self._run()
        self.assertEqual(self.calls, 4)

    def test_import_all_or_nothing_counts(self):
        self._run()
        conn = connect(":memory:")
        init_db(conn)
        seed_reference(conn)
        r1 = anon.import_history(conn)
        r2 = anon.import_history(conn)
        self.assertEqual(r1, {"added": 4, "duplicates": 0, "rejected": 0})
        self.assertEqual(r2["added"], 0)
        events = read_events(conn, types=["invoice.read"])
        self.assertEqual(events[0].actor, "system")
        self.assertEqual(events[0].at, "2026-10-01T08:45:00.000Z")
        self.assertEqual(events[0].payload["lines"][0].get("product_id"), "cola-330")
        conn.close()


if __name__ == "__main__":
    unittest.main()
