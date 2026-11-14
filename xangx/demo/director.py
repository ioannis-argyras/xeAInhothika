"""Director for rehearsals and backup: reset_demo and one function per step of the 3-minute script.

Every step calls the same code as the live flow (camera, inbox, chat, delivery). It only skips
the person. A step never raises: it returns {"ok": bool, "summary": "one Greek sentence"}.
"""

import os
import shutil
import struct
import threading
import time
import traceback
import zlib
from datetime import datetime
from pathlib import Path

ORDER_ID = "P-0318"
CAMERA_ID = "cam-1"
COLA = "cola-330"
WATER = "water-1500x6"
# Products of the order that arrive in cases (the case barcode is in supplier_products).
CASE_PRODUCTS = ("cola-330", "chips-salt-90", "choco-milk-100", "juice-orange-1l")
WATER_SCANS = 9  # one six-pack short out of 10

INVOICE_FILES = {
    "A": "A-2271-ymittou.pdf",
    "B": "B-512-artopoieio.pdf",
    "G": "G-318-galakto.pdf",
}
ORDER_TEXT_1 = "Τακτοποίησε τις εκκρεμότητες."
ORDER_TEXT_2 = "Ήρθε η παράδοση του Υμηττού με το τιμολόγιο Α-2271. Τακτοποίησέ το."

PARKED = 2 ** 62
_chat_lock = threading.Lock()


def data_dir():
    return Path(os.environ.get("XANGX_DATA_DIR") or "data")


# ---------- reset ----------

def _move_aside(data, stamp):
    """Move (never delete) what is in inbox/ and outbox/ to _prin-reset/<stamp>/. Returns the count."""
    moved = 0
    for name in ("inbox", "outbox"):
        source = data / name
        if not source.is_dir():
            continue
        items = list(source.iterdir())
        if not items:
            continue
        target = data / "_prin-reset" / stamp / name
        target.mkdir(parents=True, exist_ok=True)
        for item in items:
            shutil.move(str(item), str(target / item.name))
            moved += 1
    return moved


def _clear_tables(conn):
    """Empty events and the reference tables, and park the worker cursors, in ONE transaction.

    The append-only triggers are dropped inside the transaction and put back before the commit,
    so a crash leaves the old data and the triggers. sqlite_sequence is left alone: new event ids
    keep climbing, so open screens (after_id) and workers see the new events.
    """
    # Without this, every deleted event is checked against events.caused_by (no index): 20 seconds.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        triggers = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'events'"
        ).fetchall()
        for t in triggers:
            conn.execute(f'DROP TRIGGER "{t["name"]}"')
        actors = _cursor_actors([r["actor"] for r in conn.execute("SELECT actor FROM cursors")])
        conn.execute("DELETE FROM events")
        for table in ("supplier_products", "positions", "suppliers", "products"):
            conn.execute(f"DELETE FROM {table}")
        # Workers must not read the history while it is being written (they would replay thousands
        # of events and keep the database busy): their cursors wait far ahead until the seed is done.
        for actor in actors:
            conn.execute(
                "INSERT INTO cursors (actor, last_id) VALUES (?, ?) "
                "ON CONFLICT(actor) DO UPDATE SET last_id = excluded.last_id", (actor, PARKED))
        for t in triggers:
            conn.execute(t["sql"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return actors


def _cursor_actors(old_actors):
    """Workers that read the log. They start from the end of the seeded history, not from 0:
    otherwise the Telegram notifier would replay the whole history to the owner's phone."""
    names = {"stock", "accountant"} | set(old_actors)
    try:
        from xangx.master.telegram import NOTIFIER_ACTOR

        names.add(NOTIFIER_ACTOR)
    except ImportError:
        pass
    return sorted(names)


def reset_demo(conn, data=None, now=None):
    """Rebuild the shop as of this morning, in the same database, with the server running.

    Returns {"ms": duration, "moved_files": how many files were set aside, "events": count after}.
    """
    from xangx.demo.seed import seed_all
    from xangx.master.agent import morning_check
    from xangx.shared.cursors import set_cursor
    from xangx.shared.db import init_db

    started = time.monotonic()
    data = Path(data) if data is not None else data_dir()
    init_db(conn)
    moved = _move_aside(data, datetime.now().strftime("%Y%m%d-%H%M%S"))
    actors = _clear_tables(conn)
    seed_all(conn, now)
    last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    for actor in actors:
        set_cursor(conn, actor, last)
    morning_check(conn)
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"ms": round((time.monotonic() - started) * 1000), "moved_files": moved, "events": count}


# ---------- steps ----------

def _result(ok, summary, **extra):
    return dict(extra, ok=ok, summary=summary)


def _fail(exc):
    """The reason, in Greek. Our own errors already are Greek; anything else stays generic."""
    traceback.print_exc()
    text = str(exc)
    if any("Α" <= ch <= "ω" for ch in text):
        return _result(False, text)
    return _result(False, f"Κάτι πήγε στραβά ({type(exc).__name__}). Δες το log του server.")


def _png(label_seed):
    """A small grey PNG with the word ΠΡΟΣΟΜΟΙΩΣΗ and a strip of cells that differs every time
    (the file name is its hash: two identical photos would be treated as the same photo)."""
    glyphs = {
        "Π": ["#####", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#"],
        "Ρ": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
        "Ο": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
        "Σ": ["#####", "#....", ".#...", "..#..", ".#...", "#....", "#####"],
        "Μ": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
        "Ι": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
        "Ω": [".###.", "#...#", "#...#", "#...#", ".#.#.", ".#.#.", "##.##"],
        "Η": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    }
    word = "ΠΡΟΣΟΜΟΙΩΣΗ"
    scale, width, height = 4, 320, 120
    pixels = [[200] * width for _ in range(height)]
    x0, y0 = (width - len(word) * 6 * scale + scale) // 2, 30
    for i, letter in enumerate(word):
        for gy, row in enumerate(glyphs[letter]):
            for gx, cell in enumerate(row):
                if cell == "#":
                    for dy in range(scale):
                        for dx in range(scale):
                            pixels[y0 + gy * scale + dy][x0 + (i * 6 + gx) * scale + dx] = 40
    bits = int.from_bytes(os.urandom(8), "big") ^ (time.time_ns() & 0xFFFFFFFFFFFFFFFF) ^ label_seed
    for b in range(64):
        shade = 255 if (bits >> b) & 1 else 90
        for dy in range(4):
            for dx in range(4):
                pixels[90 + dy][32 + b * 4 + dx] = shade
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def step_kamera(conn, background=True):
    """0:20 · The camera sees 5 Coca-Cola taken. Same code as the phone upload, count given ready."""
    from xangx.stock import camera

    try:
        positions = camera.camera_positions(conn, CAMERA_ID)
        cola = next((p for p in positions if p["product_id"] == COLA), None)
        if cola is None:
            return _result(False, "Δεν βρήκα τη θέση της Coca-Cola στην κάμερα cam-1. Ξαναστήσε το demo.")

        def counted(prev_path, new_path, _positions):
            return [{"position_id": cola["position_id"], "taken": 5, "added": 0, "confidence": "high"}]

        # The "before" photo: remembered as the camera's last one, no comparison (so no events).
        camera.remember_photo(CAMERA_ID, camera.save_photo(_png(1)))
        done = camera.handle_photo(conn, CAMERA_ID, _png(2), count=counted)
        if not done.get("event_ids"):
            return _result(False, "Η φωτογραφία δεν έγραψε γεγονός. Δες το log του server.")
        return _result(True, "Η κάμερα είδε 5 Coca-Cola λιγότερες στο Ψυγείο 1.", event_ids=done["event_ids"])
    except Exception as exc:
        return _fail(exc)


def _copy_invoices(keys):
    source, inbox = data_dir() / "demo_invoices", data_dir() / "inbox"
    missing = [INVOICE_FILES[k] for k in keys if not (source / INVOICE_FILES[k]).is_file()]
    if missing:
        return None, "Τρέξε πρώτα: python scripts/make_demo_invoices.py --pdf"
    inbox.mkdir(parents=True, exist_ok=True)
    copied = []
    for k in keys:
        name = INVOICE_FILES[k]
        target = inbox / name
        if target.exists():
            continue
        shutil.copy2(source / name, target)
        copied.append(name)
    return copied, None


def step_email(conn, background=True):
    """0:50 · The 2 invoices (bakery, dairy) arrive by email: files in the inbox, the worker reads them."""
    try:
        copied, error = _copy_invoices(("B", "G"))
        if error:
            return _result(False, error)
        return _result(True, "Τα 2 τιμολόγια (αρτοποιία, γαλακτοκομικά) μπήκαν στο email. Σε λίγα δευτερόλεπτα διαβάζονται.")
    except Exception as exc:
        return _fail(exc)


def step_timologio(conn, background=True):
    """1:45 · Invoice Α-2271 arrives by email."""
    try:
        copied, error = _copy_invoices(("A",))
        if error:
            return _result(False, error)
        return _result(True, "Το τιμολόγιο Α-2271 μπήκε στο email. Σε λίγα δευτερόλεπτα διαβάζεται.")
    except Exception as exc:
        return _fail(exc)


def _chat(text, db_path, background):
    """The same call as POST /api/chat. In the background when the owner's answer can take 45 s."""
    def work():
        from xangx.master.agent import handle_owner_message
        from xangx.shared.db import connect, init_db

        own = connect(db_path)
        try:
            init_db(own)
            handle_owner_message(own, text, "web")
        except Exception:
            traceback.print_exc()
        finally:
            own.close()
            _chat_lock.release()

    if not _chat_lock.acquire(blocking=False):
        return _result(False, "Ο συνεργάτης δουλεύει ακόμα στην προηγούμενη εντολή. Περίμενε λίγο.")
    if background:
        threading.Thread(target=work, name="director-chat", daemon=True).start()
        return _result(True, "Η εντολή στάλθηκε. Η απάντηση φαίνεται στον προβολέα.")
    work()
    return _result(True, "Η εντολή στάλθηκε και ο συνεργάτης απάντησε.")


def _db_path(conn):
    row = conn.execute("PRAGMA database_list").fetchone()
    return row["file"] or ":memory:"


def step_entoli(conn, background=True):
    """0:50 · Owner: "Τακτοποίησε τις εκκρεμότητες."."""
    try:
        return _chat(ORDER_TEXT_1, _db_path(conn), background)
    except Exception as exc:
        return _fail(exc)


def step_entoli2(conn, background=True):
    """1:50 · Owner: the delivery arrived with invoice Α-2271."""
    try:
        return _chat(ORDER_TEXT_2, _db_path(conn), background)
    except Exception as exc:
        return _fail(exc)


def step_paralavi(conn, background=True):
    """1:45 · Delivery P-0318: scan the case barcodes and 9 of 10 water six-packs, then close it.

    The delivery is closed only if exactly one six-pack of water is missing."""
    from xangx.stock import delivery

    try:
        delivery_id = delivery.delivery_id_for(ORDER_ID)
        state = delivery.start(conn, ORDER_ID)
        if state["completed"]:
            return _result(True, "Η παραλαβή P-0318 έχει ήδη κλείσει.")
        rows = conn.execute(
            "SELECT product_id, case_barcode FROM supplier_products "
            "WHERE supplier_id = 'sup-ymittou' AND case_barcode IS NOT NULL"
        ).fetchall()
        case_codes = {r["product_id"]: r["case_barcode"] for r in rows}
        water = conn.execute("SELECT barcode FROM products WHERE id = ?", (WATER,)).fetchone()["barcode"]
        scanned = {l["product_id"]: l["scanned"] for l in state["lines"]}
        for pid in CASE_PRODUCTS:
            code = case_codes.get(pid)
            if code and not scanned.get(pid) and code not in state["unknown_barcodes"]:
                state = delivery.scan(conn, delivery_id, code)
        for _ in range(max(0, WATER_SCANS - scanned.get(WATER, 0))):
            state = delivery.scan(conn, delivery_id, water)
        state = delivery.status(conn, delivery_id)
        missing = [(m["product_id"], m["qty"]) for m in state["missing"]]
        if missing != [(WATER, 1)]:
            return _result(False, "Περιμένει τον μετρητή κιβωτίων του Σταύρου: η παραλαβή δείχνει "
                           "λείψεις και δεν την έκλεισα.", missing=missing)
        delivery.complete(conn, delivery_id)
        return _result(True, "Η παραλαβή έκλεισε: ήρθαν 9 από 10 εξάδες νερό, λείπει 1.")
    except Exception as exc:
        return _fail(exc)


STEPS = (
    {"key": "kamera", "time": "0:20", "label": "Κάμερα: πήραν 5 Coca-Cola", "run": step_kamera},
    {"key": "email", "time": "0:50", "label": "Τα 2 τιμολόγια στο email", "run": step_email},
    {"key": "entoli", "time": "0:50", "label": "Εντολή: Τακτοποίησε τις εκκρεμότητες", "run": step_entoli},
    {"key": "paralavi", "time": "1:45", "label": "Παράδοση P-0318: σκανάρισμα", "run": step_paralavi},
    {"key": "timologio", "time": "1:45", "label": "Τιμολόγιο Α-2271 με email", "run": step_timologio},
    {"key": "entoli2", "time": "1:50", "label": "Εντολή: Τακτοποίησε την παράδοση", "run": step_entoli2},
)
STEP_KEYS = tuple(s["key"] for s in STEPS)


def run_step(key, conn, background=True):
    """Run one step by key. Unknown key: KeyError."""
    for step in STEPS:
        if step["key"] == key:
            return step["run"](conn, background=background)
    raise KeyError(key)
