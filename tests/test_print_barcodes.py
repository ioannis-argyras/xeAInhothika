import importlib.util
import re
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "print_case_barcodes",
    Path(__file__).resolve().parent.parent / "scripts" / "print_case_barcodes.py")
pcb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcb)

CASES = ["2000003180011", "2000003180028", "2000003180035", "2000003180042", "2000003180059"]


class CheckDigitTest(unittest.TestCase):
    def test_the_five_case_barcodes_are_valid(self):
        for code in CASES:
            self.assertTrue(pcb.is_valid_ean13(code), code)

    def test_changed_digit_is_invalid(self):
        self.assertFalse(pcb.is_valid_ean13("2000003180012"))
        self.assertFalse(pcb.is_valid_ean13("2000003180"))

    def test_wrong_barcode_stops_with_clear_message(self):
        with self.assertRaises(SystemExit) as cm:
            pcb.verify([("x", "X", 1, "S", "2000003180012")])
        self.assertIn("2000003180012", str(cm.exception))
        self.assertIn("1", str(cm.exception))


class SvgTest(unittest.TestCase):
    CODE = "4006381333931"

    def test_95_modules_with_guards(self):
        m = pcb.ean13_modules(self.CODE)
        self.assertEqual(len(m), 95)
        self.assertEqual(m[:3], "101")
        self.assertEqual(m[45:50], "01010")
        self.assertEqual(m[-3:], "101")
        # first digit 4 -> parity LGLLGG; first left digit 0 in L set
        self.assertEqual(m[3:10], "0001101")
        # last digit 1 in R set
        self.assertEqual(m[-10:-3], "1100110")

    def test_svg_bars_match_modules(self):
        svg = pcb.ean13_svg(self.CODE)
        modules = pcb.ean13_modules(self.CODE)
        drawn = ["0"] * 95
        for x, w in re.findall(r'<rect x="(\d+)" y="0" width="(\d+)"', svg):
            for k in range(int(x) - pcb.QUIET_LEFT, int(x) - pcb.QUIET_LEFT + int(w)):
                drawn[k] = "1"
        self.assertEqual("".join(drawn), modules)
        self.assertIn("EAN-13 " + self.CODE, svg)


class HtmlTest(unittest.TestCase):
    def test_five_cards(self):
        cases = [("p%d" % i, "Προϊόν %d" % i, 24, "Υμηττού", c) for i, c in enumerate(CASES)]
        page = pcb.build_html(cases)
        self.assertEqual(page.count('class="card"'), 5)
        self.assertIn('lang="el"', page)
        self.assertIn("size: A4", page)
        self.assertIn("Παράδοση P-0318", page)

    def test_water_pack_adds_sixth_card(self):
        cases = [("p", "P", 24, "S", CASES[0])]
        page = pcb.build_html(cases, ("Νερό 1,5L εξάδα", "4006381333931"))
        self.assertEqual(page.count('class="card"'), 2)
        self.assertIn("χωριστά", page)

    def test_loose_chocolate_card(self):
        page = pcb.build_html([("p", "P", 24, "S", CASES[0])], None,
                              [("Σοκολάτα γάλακτος 100g", "4006381333931", "Λιανική, σκανάρεται 6 φορές")])
        self.assertEqual(page.count('class="card"'), 2)
        self.assertIn("Σοκολάτα γάλακτος 100g · χωριστά", page)
        self.assertIn("σκανάρεται 6 φορές", page)


if __name__ == "__main__":
    unittest.main()
