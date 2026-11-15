"""Fake demo catalog: one small shop, 36 products, 4 suppliers, 32 shelf positions.

Everything here is invented, except the public retail barcode of the Coca-Cola can.
Tax numbers (AFM) and IBANs are generated with valid check digits but belong to nobody.
"""

STORE_NAME = "Mini Market 24/7, Παγκράτι"

# id, name, category, vat, sell price (gross cents), low_threshold,
# cost (net cents, main supplier), popularity weight, pack size, main supplier id
_Y, _K, _A, _G = "sup-ymittou", "sup-kava", "sup-artopoieio", "sup-galakto"

_PRODUCT_ROWS = [
    ("cola-330", "Coca-Cola 330ml κουτί", "Αναψυκτικά", 24, 100, 6, 57, 6.0, 24, _Y),
    ("cola-zero-330", "Coca-Cola Zero 330ml κουτί", "Αναψυκτικά", 24, 100, 6, 57, 3.0, 24, _Y),
    ("sprite-330", "Sprite 330ml κουτί", "Αναψυκτικά", 24, 100, 6, 57, 3.0, 24, _Y),
    ("fanta-330", "Fanta πορτοκάλι 330ml κουτί", "Αναψυκτικά", 24, 100, 6, 57, 2.0, 24, _Y),
    ("ice-tea-lemon-500", "Ice tea λεμόνι 500ml", "Αναψυκτικά", 24, 130, 6, 82, 2.0, 12, _Y),
    ("cola-1500", "Coca-Cola 1,5L φιάλη", "Αναψυκτικά", 24, 220, 6, 140, 1.5, 6, _Y),
    ("energy-250", "Ενεργειακό ποτό 250ml", "Αναψυκτικά", 24, 150, 6, 95, 2.0, 24, _Y),
    ("water-500", "Νερό 500ml", "Νερά", 13, 50, 1, 24, 6.0, 24, _K),
    ("water-1500", "Νερό 1,5L", "Νερά", 13, 80, 10, 38, 2.5, 6, _K),
    ("water-1500x6", "Νερό 1,5L εξάδα", "Νερά", 13, 380, 4, 215, 0.8, 1, _Y),
    ("water-sparkling-500", "Ανθρακούχο νερό 500ml", "Νερά", 13, 90, 6, 48, 1.0, 12, _K),
    ("juice-orange-1l", "Χυμός πορτοκάλι 1L", "Χυμοί", 13, 240, 4, 150, 1.2, 12, _Y),
    ("juice-apple-1l", "Χυμός μήλο 1L", "Χυμοί", 13, 240, 4, 150, 0.8, 12, _K),
    ("juice-mixed-1l", "Χυμός πολλαπλών φρούτων 1L", "Χυμοί", 13, 240, 4, 150, 0.8, 12, _K),
    ("juice-orange-250", "Χυμός πορτοκάλι 250ml", "Χυμοί", 13, 110, 6, 65, 1.5, 24, _Y),
    ("chips-salt-90", "Πατατάκια αλάτι 90g", "Snacks", 24, 190, 8, 105, 4.0, 12, _Y),
    ("chips-oregano-90", "Πατατάκια ρίγανη 90g", "Snacks", 24, 190, 8, 105, 3.0, 12, _Y),
    ("chips-paprika-90", "Πατατάκια πάπρικα 90g", "Snacks", 24, 190, 8, 105, 2.5, 12, _Y),
    ("nuts-mixed-100", "Ξηροί καρποί ανάμεικτοι 100g", "Snacks", 24, 220, 5, 135, 1.0, 12, _Y),
    ("pretzel-sesame", "Κουλούρι σουσάμι", "Snacks", 13, 70, 10, 38, 3.0, 20, _A),
    ("biscuit-butter-200", "Μπισκότα βουτύρου 200g", "Snacks", 24, 180, 6, 100, 1.5, 12, _A),
    ("cake-choco", "Κέικ σοκολάτα μερίδα", "Snacks", 24, 120, 6, 66, 1.5, 12, _A),
    ("choco-milk-100", "Σοκολάτα γάλακτος 100g", "Σοκολάτες", 24, 220, 8, 125, 3.0, 24, _Y),
    ("choco-dark-100", "Σοκολάτα υγείας 100g", "Σοκολάτες", 24, 240, 6, 140, 1.5, 24, _Y),
    ("choco-hazel-100", "Σοκολάτα με φουντούκια 100g", "Σοκολάτες", 24, 240, 6, 140, 1.5, 24, _Y),
    ("choco-bar-45", "Σοκολάτα μπάρα 45g", "Σοκολάτες", 24, 110, 12, 62, 3.5, 36, _Y),
    ("milk-fresh-1l", "Γάλα φρέσκο 1L", "Γαλακτοκομικά", 13, 190, 6, 115, 3.0, 12, _G),
    ("milk-choco-250", "Σοκολατούχο γάλα 250ml", "Γαλακτοκομικά", 13, 130, 6, 75, 1.5, 24, _G),
    ("yogurt-greek-200", "Γιαούρτι στραγγιστό 200g", "Γαλακτοκομικά", 13, 120, 8, 68, 2.5, 24, _G),
    ("cheese-slices-200", "Τυρί τοστ φέτες 200g", "Γαλακτοκομικά", 13, 320, 4, 195, 1.0, 10, _G),
    ("butter-250", "Βούτυρο 250g", "Γαλακτοκομικά", 13, 310, 3, 190, 0.6, 10, _G),
    ("coffee-frappe-can", "Cold brew κουτάκι 250ml", "Καφές", 24, 160, 1, 95, 2.0, 24, _Y),
    ("coffee-ground-194", "Espresso κόκκοι, ποικιλία Α", "Καφές", 24, 450, 1, 290, 0.8, 12, _Y),
    ("coffee-capsule-10", "Κάψουλες καφέ 10 τεμ.", "Καφές", 24, 420, 3, 270, 0.6, 12, _Y),
    ("coffee-instant-100", "Στιγμιαίος καφές 100g", "Καφές", 24, 520, 3, 340, 0.5, 12, _Y),
    # Appended at the end so the invented barcodes of the other products do not move.
    ("coffee-espresso-b", "Espresso κόκκοι, ποικιλία Β", "Καφές", 24, 450, 1, 290, 0.8, 12, _Y),
]

# Shelf life in days from the day the goods enter the store. Not listed = never expires.
_SHELF_LIFE_DAYS = {
    "milk-fresh-1l": 7, "yogurt-greek-200": 21,
    "juice-orange-1l": 30, "juice-apple-1l": 30, "juice-mixed-1l": 30, "juice-orange-250": 30,
    "cola-330": 270, "cola-zero-330": 270, "sprite-330": 270, "fanta-330": 270,
    "ice-tea-lemon-500": 270, "cola-1500": 270, "energy-250": 270,
    "water-500": 365, "water-1500": 365, "water-1500x6": 365, "water-sparkling-500": 365,
    "chips-salt-90": 180, "chips-oregano-90": 180, "chips-paprika-90": 180,
    "nuts-mixed-100": 180, "biscuit-butter-200": 180,
    "choco-milk-100": 270, "choco-dark-100": 270, "choco-hazel-100": 270, "choco-bar-45": 270,
}

# Public retail barcode of the Coca-Cola can; the others are invented (valid EAN-13).
_PUBLIC_BARCODES = {"cola-330": "5449000000996"}


def ean13(body12):
    """Append the EAN-13 check digit to 12 digits."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


def afm_valid(afm):
    """Greek tax number check: 9 digits, check digit by mod 11."""
    if not (isinstance(afm, str) and len(afm) == 9 and afm.isdigit()):
        return False
    total = sum(int(afm[i]) * 2 ** (8 - i) for i in range(8))
    return total % 11 % 10 == int(afm[8])


def make_afm(base8):
    """Add the check digit to 8 digits."""
    total = sum(int(base8[i]) * 2 ** (8 - i) for i in range(8))
    return base8 + str(total % 11 % 10)


def iban_valid(iban):
    """IBAN check by mod 97."""
    text = iban.replace(" ", "").upper()
    if len(text) < 8 or not text.isalnum():
        return False
    moved = text[4:] + text[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def make_iban(bban):
    """Build a Greek IBAN (invented) from the 23-digit BBAN."""
    digits = "".join(str(int(ch, 36)) for ch in bban + "GR00")
    check = 98 - int(digits) % 97
    return f"GR{check:02d}{bban}"


PRODUCTS = [
    {
        "id": pid,
        "name": name,
        "barcode": _PUBLIC_BARCODES.get(pid, ean13(f"5209{900 + i:03d}{i:05d}")),
        "category": category,
        "vat_rate": vat,
        "sell_price_cents": price,
        "low_threshold": low,
        "cost_cents": cost,
        "weight": weight,
        "pack_size": pack,
        "main_supplier": supplier,
        "shelf_life_days": _SHELF_LIFE_DAYS.get(pid),
    }
    for i, (pid, name, category, vat, price, low, cost, weight, pack, supplier)
    in enumerate(_PRODUCT_ROWS, start=1)
]
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}

SUPPLIERS = [
    {
        "id": _Y, "name": "Διανομές Υμηττού Α.Ε.", "afm": make_afm("99000001"),
        "email": "paraggelies.ymittou@example.com", "phone": "210 0000 101",
        "iban": make_iban("00000010000000000000101"), "credit_days": 30,
        "interval_days": 3, "offset": 0, "series": "Α",
    },
    {
        "id": _K, "name": "Κάβα Βύρωνα", "afm": make_afm("99000002"),
        "email": "paraggelies.kava@example.com", "phone": "210 0000 102",
        "iban": make_iban("00000020000000000000102"), "credit_days": 0,
        "interval_days": 6, "offset": 4, "series": "ΤΔΑ",
    },
    {
        "id": _A, "name": "Αρτοποιία Λαμπρόπουλου", "afm": make_afm("99000003"),
        "email": "fournos@example.com", "phone": "210 0000 103",
        "iban": make_iban("00000030000000000000103"), "credit_days": 0,
        "interval_days": 6, "offset": 2, "series": "Β",
    },
    {
        "id": _G, "name": "Γαλακτοκομικά Κορωπίου", "afm": make_afm("99000004"),
        "email": "paraggelies.galakto@example.com", "phone": "210 0000 104",
        "iban": make_iban("00000040000000000000104"), "credit_days": 14,
        "interval_days": 6, "offset": 5, "series": "Γ",
    },
]
SUPPLIER_BY_ID = {s["id"]: s for s in SUPPLIERS}

# Products that the second supplier also carries (at a different price).
_KAVA_ALSO = [
    "cola-330", "cola-zero-330", "sprite-330", "fanta-330", "water-1500x6",
    "juice-orange-1l", "energy-250",
]
# Products that the first supplier also carries.
_Y_ALSO = ["water-500", "water-1500", "juice-apple-1l", "juice-mixed-1l"]


def _supplier_price(supplier_id, product):
    """Net unit cost in cents for one supplier. Cola stays at 0,57 EUR at the main one."""
    cost = product["cost_cents"]
    if supplier_id == product["main_supplier"]:
        return cost
    if supplier_id == _K:
        return cost + 3 if product["id"] != "cola-330" else cost + 4
    return cost + 2  # first supplier as second source for Kava products


# Case (carton) barcodes of the products on order P-0318: invented EAN-13, prefix 200
# (internal use, not real), different from every product barcode.
CASE_BARCODES = {
    ("sup-ymittou", "cola-330"): ean13("200000318001"),
    ("sup-ymittou", "water-1500x6"): ean13("200000318002"),
    ("sup-ymittou", "chips-salt-90"): ean13("200000318003"),
    ("sup-ymittou", "choco-milk-100"): ean13("200000318004"),
    ("sup-ymittou", "juice-orange-1l"): ean13("200000318005"),
}

SUPPLIER_PRODUCTS = []
for _p in PRODUCTS:
    _suppliers = [_p["main_supplier"]]
    if _p["id"] in _KAVA_ALSO:
        _suppliers.append(_K)
    if _p["id"] in _Y_ALSO:
        _suppliers.append(_Y)
    for _s in _suppliers:
        SUPPLIER_PRODUCTS.append(
            {
                "supplier_id": _s,
                "product_id": _p["id"],
                "supplier_code": f"{_s[4:7].upper()}-{_p['barcode'][-5:]}",
                "pack_size": _p["pack_size"],
                "case_barcode": CASE_BARCODES.get((_s, _p["id"])),
                "unit_cents": _supplier_price(_s, _p),
            }
        )
SUPPLIER_PRICE = {
    (sp["supplier_id"], sp["product_id"]): sp["unit_cents"] for sp in SUPPLIER_PRODUCTS
}

# 32 positions: 4 in Fridge 1 (camera cam-1), the rest on shelves.
_FRIDGE = ["coffee-frappe-can", "coffee-ground-194", "coffee-espresso-b", "water-500"]
_SHELF_PRODUCTS = [
    p["id"] for p in PRODUCTS
    if p["id"] not in _FRIDGE
    and p["id"] not in ("water-1500x6", "coffee-capsule-10", "coffee-instant-100",
                        "butter-250")
]  # 28 products; the other 4 are kept in the back room

POSITIONS = [
    {"id": f"fridge1-r{i}", "name": f"Ψυγείο 1 · {i}η σειρά", "product_id": pid,
     "camera_id": "cam-1"}
    for i, pid in enumerate(_FRIDGE, start=1)
] + [
    {"id": f"shelf-{(i - 1) // 5 + 1}{chr(96 + (i - 1) % 5 + 1)}",
     "name": f"Ράφι {(i - 1) // 5 + 1} · θέση {(i - 1) % 5 + 1}",
     "product_id": pid,
     "camera_id": f"cam-{(i - 1) // 5 + 2}"}
    for i, pid in enumerate(_SHELF_PRODUCTS, start=1)
]
