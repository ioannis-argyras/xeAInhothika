"""Anonymize the real invoices in pragmatika/ into demo history. Runs only on Manolis's laptop.

    python tools/anonymize/anonymize.py run       read (cached), blocklist, anonymize, leak check
    python tools/anonymize/anonymize.py import    write the anonymized history as invoice.read events

Real data never leaves pragmatika/: the raw readings are cached in pragmatika/.cache/ and the
anonymized result goes to pragmatika/anon/history.json. Real names, AFMs and IBANs are added
to .git/xangx-fylakas/blocklist.local.txt so the commit guard blocks them. This script prints
only counts, never real values.

Suppliers become "Προμηθευτής Α", "Προμηθευτής Β"... with fake AFMs and IBANs that have valid
check digits. Prices, quantities and dates stay the same.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from xangx.demo.catalog_data import afm_valid, iban_valid, make_afm, make_iban  # noqa: E402

REAL = ROOT / "pragmatika"
CACHE = REAL / ".cache"
OUT = REAL / "anon" / "history.json"
BLOCKLIST = ROOT / ".git" / "xangx-fylakas" / "blocklist.local.txt"
EXT = {".pdf", ".jpg", ".jpeg", ".png"}
LETTERS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
FAKE_AFM_BASE = 97000000   # fake AFMs 97xxxxxx + check digit
MIN_NAME = 4               # shorter names are not blocked (too many false hits)


def fake_supplier(index):
    letter = LETTERS[index] if index < len(LETTERS) else f"Χ{index}"
    return {
        "name": f"Προμηθευτής {letter}",
        "afm": make_afm(str(FAKE_AFM_BASE + index + 1)),
        "iban": make_iban(f"{index + 1:07d}" + "0" * 12 + f"{index + 1:04d}"),
    }


# ---------- 1. reading, cached so each file is read once ----------

def real_files():
    return sorted(p for p in REAL.iterdir() if p.is_file() and p.suffix.lower() in EXT)


def read_all(reader):
    CACHE.mkdir(parents=True, exist_ok=True)
    out, new = [], 0
    for path in real_files():
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        cached = CACHE / f"{sha}.json"
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
        else:
            data = reader(path)
            cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            new += 1
        out.append((sha, data))
    return out, new


# ---------- 2. blocklist terms (never printed) ----------

def _digits(text):
    return re.sub(r"\D", "", str(text or ""))


def _name_pattern(name):
    words = [re.escape(w) for w in re.split(r"\s+", str(name).strip()) if w]
    return r"\s*".join(words)


# Common business words: not identifying, and blocking them would hit normal text.
STOP_WORDS = {
    "τρόφιμα", "τροφίμων", "εμπορική", "εμπορικη", "εμπόριο", "διανομές", "διανομη", "διανομή",
    "market", "μάρκετ", "μαρκετ", "super", "αρτοποιία", "αρτοποιείο", "γαλακτοκομικά", "ποτών",
    "εταιρεία", "εταιρια", "ανώνυμη", "ομόρρυθμη", "company", "χονδρικό", "χονδρικής",
    "λιανικό", "λιανικής", "προϊόντα", "προϊόντων", "υπηρεσίες",
}
MIN_WORD = 6


def sensitive_terms(readings):
    """Regex lines for the guard: names (whole and their distinctive words), AFMs, IBANs."""
    terms = set()
    for _, d in readings:
        for key in ("supplier_name", "customer_name"):
            name = (d.get(key) or "").strip()
            if len(name) >= MIN_NAME:
                terms.add(_name_pattern(name))
            for word in re.findall(r"\w+", name):
                if len(word) >= MIN_WORD and not word.isdigit() and word.casefold() not in STOP_WORDS:
                    terms.add(rf"\b{re.escape(word)}\b")
        for key in ("supplier_afm", "customer_afm"):
            afm = _digits(d.get(key))
            if len(afm) == 9:
                terms.add(rf"\b{afm}\b")
        iban = re.sub(r"\s", "", str(d.get("iban") or "")).upper()
        if len(iban) >= 15:
            terms.add(r"\s?".join(re.escape(ch) for ch in iban))
    return terms


def update_blocklist(terms):
    BLOCKLIST.parent.mkdir(parents=True, exist_ok=True)
    existing = BLOCKLIST.read_text(encoding="utf-8").splitlines() if BLOCKLIST.exists() else []
    have = set(existing)
    new = [t for t in sorted(terms) if t not in have]
    if new:
        with BLOCKLIST.open("a", encoding="utf-8") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("\n".join(new) + "\n")
    return len(new)


# ---------- 3. anonymize ----------

def anonymize(readings):
    """Return (invoices, suppliers). Same prices and dates, fake identities."""
    mapping = {}
    invoices = []
    for n, (sha, d) in enumerate(readings, 1):
        afm = _digits(d.get("supplier_afm"))
        key = afm or (d.get("supplier_name") or f"unknown-{sha[:6]}")
        if key not in mapping:
            mapping[key] = fake_supplier(len(mapping))
        fake = mapping[key]
        lines = []
        for line in d.get("lines") or []:
            lines.append({k: line.get(k) for k in ("name", "qty", "unit_cents", "net_cents", "vat_rate")})
        invoices.append({
            "invoice_id": f"INV-H{n:03d}",
            "supplier_afm": fake["afm"],
            "series": str(d.get("series") or "").strip() or "Α",
            "number": str(d.get("number") or n).strip(),
            "date": str(d.get("date") or ""),
            "lines": lines,
            "net_cents": d.get("net_cents"),
            "vat_by_rate": d.get("vat_by_rate") or {},
            "total_cents": d.get("total_cents"),
            "iban": fake["iban"] if d.get("iban") else None,
        })
    return invoices, list(mapping.values())


def leak_hits(text, terms):
    return sum(1 for t in terms if re.search(t, text, re.IGNORECASE))


def scrub(invoices, terms):
    """Replace any real term left in line names (e.g. supplier name inside a product line)."""
    fixed = 0
    for inv in invoices:
        for line in inv["lines"]:
            name = str(line.get("name") or "")
            for t in terms:
                name, k = re.subn(t, "…", name, flags=re.IGNORECASE)
                fixed += k
            line["name"] = name
    return fixed


def run(reader=None):
    if reader is None:
        from xangx.invoices.read import read_invoice as reader
    readings, new = read_all(reader)
    terms = sensitive_terms(readings)
    added = update_blocklist(terms)
    invoices, suppliers = anonymize(readings)
    scrubbed = scrub(invoices, terms)
    result = {"suppliers": suppliers, "invoices": invoices}
    text = json.dumps(result, ensure_ascii=False)
    hits = leak_hits(text, terms)
    checks_ok = all(afm_valid(s["afm"]) and iban_valid(s["iban"]) for s in suppliers)
    if hits:
        print(f"ΣΤΑΜΑΤΗΣΑ: έμειναν {hits} πραγματικοί όροι στο αποτέλεσμα. Τίποτα δεν γράφτηκε.")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Αρχεία: {len(readings)} (νέες αναγνώσεις: {new})")
    print(f"Όροι στη λίστα του φύλακα: {len(terms)} (νέοι: {added})")
    print(f"Ανωνυμοποιημένοι προμηθευτές: {len(suppliers)}, τιμολόγια: {len(invoices)}")
    print(f"Αντικαταστάσεις μέσα σε γραμμές: {scrubbed}")
    print(f"Έλεγχος διαρροής: 0 πραγματικοί όροι · ψηφία ελέγχου ΑΦΜ/IBAN: {'OK' if checks_ok else 'ΛΑΘΟΣ'}")
    print(f"Έξοδος: {OUT.relative_to(ROOT)}")
    return 0 if checks_ok else 1


# ---------- 4. import as history ----------

def import_history(conn):
    """All or nothing: invoice.read events, actor system, `at` = invoice date 08:45 UTC."""
    from xangx.invoices.core import guess_product
    from xangx.shared import append_events

    data = json.loads(OUT.read_text(encoding="utf-8"))
    items, rejected = [], 0
    for inv in data["invoices"]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", inv["date"] or "") or inv["total_cents"] is None:
            rejected += 1
            continue
        lines = []
        for line in inv["lines"]:
            out = dict(line)
            pid = guess_product(conn, None, line.get("name"))
            if pid:
                out["product_id"] = pid
            lines.append(out)
        net = inv["net_cents"] or 0
        arithmetic = (abs(sum(l.get("net_cents") or 0 for l in lines) - net) <= 2
                      and abs(net + sum((inv["vat_by_rate"] or {}).values()) - inv["total_cents"]) <= 2)
        payload = dict(inv, lines=lines, confidence="high" if arithmetic else "low",
                       checks={"arithmetic": arithmetic, "afm_valid": True, "pages_complete": True})
        if payload["iban"] is None:
            payload.pop("iban")
        items.append(dict(
            type="invoice.read", actor="system", payload=payload,
            at=f"{inv['date']}T08:45:00.000Z", subject=f"invoice:{inv['invoice_id']}",
            idem_key=f"invoice:{inv['supplier_afm']}:{inv['series']}-{inv['number']}",
        ))
    results = append_events(conn, items) if items else []
    added = sum(1 for _, created in results if created)
    return {"added": added, "duplicates": len(results) - added, "rejected": rejected}


def main():
    os.chdir(ROOT)
    from xangx.shared.env import load_env

    load_env(".env")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        return run()
    if cmd == "import":
        from xangx.shared import connect, init_db

        conn = connect()
        init_db(conn)
        r = import_history(conn)
        print(f"Ιστορικό: προστέθηκαν {r['added']}, διπλά {r['duplicates']}, απορρίφθηκαν {r['rejected']}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
