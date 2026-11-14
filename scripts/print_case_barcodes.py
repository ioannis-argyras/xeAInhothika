"""Print the case barcodes of delivery P-0318 on one A4 page (stickers for the stage boxes).

Run from the repo root: python scripts/print_case_barcodes.py [--out data/print/kivotia-P-0318.html]

Reads supplier_products.case_barcode from the database (XANGX_DB, else data/xangx.sqlite).
If the database has none, it uses the built-in list and says so. Every EAN-13 check
digit is verified first: a wrong barcode stops the script, nothing is corrected.
"""

import html
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xangx.shared import connect  # noqa: E402

DELIVERY = "P-0318"
DEFAULT_OUT = "data/print/kivotia-P-0318.html"

# Fallback when the database has no case barcodes: (product_id, name, pack_size, supplier, barcode)
FALLBACK = [
    ("cola-330", "Κόλα 330ml", 24, "Υμηττού", "2000003180011"),
    ("water-1500x6", "Νερό 1,5L εξάδα", 1, "Υμηττού", "2000003180028"),
    ("chips-salt-90", "Πατατάκια αλάτι 90g", 20, "Υμηττού", "2000003180035"),
    ("choco-milk-100", "Σοκολατούχο γάλα 100ml", 24, "Υμηττού", "2000003180042"),
    ("juice-orange-1l", "Χυμός πορτοκάλι 1L", 12, "Υμηττού", "2000003180059"),
]

# EAN-13 encoding tables (digits 0-9). L and G are the left-half sets, R the right half.
_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
      "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101",
      "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100",
      "1001110", "1010000", "1000100", "1001000", "1110100"]
# Parity of the 6 left digits, chosen by the first digit.
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
           "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]

QUIET_LEFT = 11
QUIET_RIGHT = 7
MODULE_MM = 0.4
BAR_HEIGHT_MM = 25.0


def check_digit(first12):
    """EAN-13 check digit of the first 12 digits (string)."""
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(first12))
    return (10 - total % 10) % 10


def is_valid_ean13(code):
    return (isinstance(code, str) and len(code) == 13 and code.isdigit()
            and check_digit(code[:12]) == int(code[12]))


def ean13_modules(code):
    """The 95 modules of an EAN-13 as a string of 0 and 1 (1 = bar)."""
    if not is_valid_ean13(code):
        raise ValueError(f"not a valid EAN-13: {code}")
    parity = _PARITY[int(code[0])]
    out = "101"
    for digit, p in zip(code[1:7], parity):
        out += (_L if p == "L" else _G)[int(digit)]
    out += "01010"
    for digit in code[7:]:
        out += _R[int(digit)]
    out += "101"
    return out


def ean13_svg(code):
    """Inline SVG of an EAN-13. Units are modules; printed size is set in mm."""
    modules = ean13_modules(code)
    total_w = QUIET_LEFT + 95 + QUIET_RIGHT
    bar_h = BAR_HEIGHT_MM / MODULE_MM
    guard_extra = 5
    font = 9
    total_h = bar_h + guard_extra + font + 2
    guards = set(range(0, 3)) | set(range(45, 50)) | set(range(92, 95))
    rects = []
    i = 0
    while i < 95:
        if modules[i] == "1":
            j = i
            while j < 95 and modules[j] == "1" and ((j in guards) == (i in guards)):
                j += 1
            h = bar_h + (guard_extra if i in guards else 0)
            rects.append(f'<rect x="{QUIET_LEFT + i}" y="0" width="{j - i}" height="{h:g}"/>')
            i = j
        else:
            i += 1
    y_text = bar_h + guard_extra + font - 1
    texts = [f'<text x="{QUIET_LEFT - 3}" y="{y_text:g}" text-anchor="middle">{code[0]}</text>']
    for k, c in enumerate(code[1:7]):
        texts.append(f'<text x="{QUIET_LEFT + 3 + 3.5 + 7 * k:g}" y="{y_text:g}" text-anchor="middle">{c}</text>')
    for k, c in enumerate(code[7:]):
        texts.append(f'<text x="{QUIET_LEFT + 50 + 3.5 + 7 * k:g}" y="{y_text:g}" text-anchor="middle">{c}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h:g}" '
        f'width="{total_w * MODULE_MM:g}mm" height="{total_h * MODULE_MM:g}mm" '
        f'role="img" aria-label="EAN-13 {code}" shape-rendering="crispEdges">'
        f'<rect width="{total_w}" height="{total_h:g}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g>'
        f'<g fill="#000" font-family="Consolas, Menlo, monospace" font-size="{font}">{"".join(texts)}</g>'
        f'</svg>'
    )


def load_cases(conn):
    """Case barcodes from the database, or the fallback list. Returns (rows, source)."""
    rows = conn.execute(
        "SELECT sp.product_id, p.name, sp.pack_size, s.name AS supplier, sp.case_barcode "
        "FROM supplier_products sp "
        "JOIN products p ON p.id = sp.product_id "
        "JOIN suppliers s ON s.id = sp.supplier_id "
        "WHERE sp.case_barcode IS NOT NULL ORDER BY sp.case_barcode"
    ).fetchall()
    if rows:
        return [tuple(r) for r in rows], "database"
    return list(FALLBACK), "fallback"


def load_water_pack(conn):
    """(name, retail barcode) of the water six-pack, or None."""
    row = conn.execute(
        "SELECT name, barcode FROM products WHERE id = 'water-1500x6' AND barcode IS NOT NULL"
    ).fetchone()
    return (row["name"], row["barcode"]) if row else None


def verify(cases, extra=()):
    for product_id, _name, _pack, _sup, code in cases:
        _verify_one(product_id, code)
    for label, code in extra:
        _verify_one(label, code)


def _verify_one(label, code):
    if not is_valid_ean13(code):
        got = code[-1] if code else "?"
        exp = check_digit(code[:12]) if code and len(code) >= 12 and code[:12].isdigit() else "?"
        raise SystemExit(
            f"Λάθος barcode στο {label}: {code}. Το ψηφίο ελέγχου είναι {got}, "
            f"ενώ θα έπρεπε να είναι {exp}. Δεν διορθώθηκε, έλεγξε τη βάση."
        )


_CSS = """
@page { size: A4; margin: 12mm }
* { box-sizing: border-box }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact }
body { margin: 0; background: #fff; color: #000; font-family: system-ui, "Segoe UI", Arial, sans-serif }
h1 { font-size: 14pt; margin: 0 0 4mm }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm }
.card { border: 0.4mm dashed #000; padding: 4mm; break-inside: avoid; display: flex; flex-direction: column; gap: 1.5mm }
.name { font-size: 15pt; font-weight: 700; line-height: 1.2 }
.meta { font-size: 10.5pt }
.card svg { display: block; margin-top: 1mm }
.cut { font-size: 7.5pt; margin-top: auto }
"""


def card_html(name, line1, line2, code, note):
    return (
        '<section class="card">'
        f'<div class="name">{html.escape(name)}</div>'
        f'<div class="meta">{html.escape(line1)}</div>'
        f'<div class="meta">{html.escape(line2)}</div>'
        f'{ean13_svg(code)}'
        f'<div class="cut">{html.escape(note)}</div>'
        '</section>'
    )


def build_html(cases, water=None):
    cards = []
    for _pid, name, pack, supplier, code in cases:
        cards.append(card_html(
            name, f"Κιβώτιο {pack} τεμ.", f"{supplier} · Παράδοση {DELIVERY}", code,
            "✂ κόψε στη διακεκομμένη γραμμή"))
    if water:
        wname, wcode = water
        cards.append(card_html(
            f"{wname} · χωριστά", "Λιανική εξάδα, σκανάρεται μία-μία", f"Παράδοση {DELIVERY}", wcode,
            "✂ κόψε στη διακεκομμένη γραμμή"))
    return (
        '<!DOCTYPE html>\n<html lang="el"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Κιβώτια {DELIVERY}</title><style>{_CSS}</style></head><body>'
        f'<h1>Κιβώτια παράδοσης {DELIVERY}</h1><main class="grid">'
        + "".join(cards) + '</main></body></html>\n'
    )


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    out = DEFAULT_OUT
    if "--out" in args:
        i = args.index("--out")
        if i + 1 >= len(args):
            print("Λείπει η τιμή μετά το --out.")
            return 2
        out = args[i + 1]
    conn = connect()
    try:
        cases, source = load_cases(conn)
        water = load_water_pack(conn)
    finally:
        conn.close()
    if source == "fallback":
        print("Η βάση δεν έχει barcode κιβωτίων: χρησιμοποιώ την εφεδρική λίστα των 5.")
    verify(cases, [("water-1500x6 (λιανική)", water[1])] if water else [])
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(cases, water), encoding="utf-8")
    print(f"{len(cases)} κιβώτια" + (" + εξάδα νερού" if water else "") + f" από: {source}")
    print(path.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
