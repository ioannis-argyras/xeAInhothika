"""Page and API of the invoices role: a photo of a paper invoice from the phone.

POST /invoices/photo takes the file as the plain request body (no multipart), checks the first
bytes, writes it to data/inbox/ and registers it. The invoices worker reads it like any other file.
GET /invoices/photo/status tells the result screen how far the reading got.
"""

import hashlib
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from xangx.invoices import core
from xangx.screen.describe import describe, load_names
from xangx.shared import connect, init_db, read_events
from xangx.shared.money import format_eur

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"
MAX_BYTES = 10 * 1024 * 1024
ASSETS = {"photo.css": "text/css; charset=utf-8", "photo.js": "text/javascript; charset=utf-8"}
NO_CACHE = {"Cache-Control": "no-cache"}


def _kind(head):
    """File extension from the first bytes, or None."""
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] == b"\x89PNG":
        return ".png"
    if head[:4] == b"%PDF":
        return ".pdf"
    return None


@router.get("/invoices/photo")
def photo_page():
    return FileResponse(STATIC / "photo.html", media_type="text/html", headers=NO_CACHE)


@router.get("/invoices/photo/assets/{name}")
def photo_asset(name: str):
    if name not in ASSETS:
        raise HTTPException(404)
    return FileResponse(STATIC / name, media_type=ASSETS[name], headers=NO_CACHE)


@router.post("/invoices/photo")
async def photo_upload(request: Request):
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BYTES:
                raise HTTPException(413, "Το αρχείο πρέπει να είναι έως 10 MB.")
        except ValueError:
            raise HTTPException(400, "Μη έγκυρο μέγεθος αρχείου.") from None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(413, "Το αρχείο πρέπει να είναι έως 10 MB.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "Δεν ήρθε αρχείο.")
    ext = _kind(data[:8])
    if ext is None:
        raise HTTPException(415, "Δέχομαι μόνο φωτογραφία JPG, PNG ή PDF.")

    inbox = core.data_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stem = f"photo-{int(time.time())}-{secrets.token_hex(4)}"
    part = inbox / f"{stem}.part"  # not an invoice extension: the worker skips it
    final = inbox / f"{stem}{ext}"
    part.write_bytes(data)
    os.replace(part, final)
    invoice_id = "INV-" + hashlib.sha256(data).hexdigest()[:8]
    conn = connect()
    try:
        init_db(conn)
        try:
            event, _created = core.ingest_file(conn, final, source="photo")
            invoice_id = event.payload["invoice_id"]
        except FileNotFoundError:
            pass  # the worker took the file between the rename and here: same id
    finally:
        conn.close()
    return {"invoice_id": invoice_id}


def _supplier_name(conn, afm):
    row = conn.execute("SELECT name FROM suppliers WHERE afm = ?", (afm,)).fetchone()
    return row["name"] if row else None


def _items(lines):
    """What the invoice says, line by line, with the amounts already in Greek format."""
    out = []
    for line in lines:
        qty = line.get("qty")
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        unit = line.get("unit_cents")
        out.append({
            "name": str(line.get("name") or ""),
            "qty": qty,
            "unit": format_eur(unit) if unit is not None else "",
            "net": format_eur(line.get("net_cents") or 0),
        })
    return out


@router.get("/invoices/photo/status")
def photo_status(invoice_id: str = ""):
    if not invoice_id or len(invoice_id) > 40 or not all(c.isalnum() or c == "-" for c in invoice_id):
        raise HTTPException(400, "Μη έγκυρο τιμολόγιο.")
    conn = connect()
    try:
        init_db(conn)
        if core._latest(conn, "invoice.received", invoice_id) is None:
            raise HTTPException(404, "Δεν βρέθηκε αυτό το τιμολόγιο.")
        names = load_names(conn)
        out = {"invoice_id": invoice_id, "read": None, "mydata": None, "issues": [], "done": False}
        read = core._latest(conn, "invoice.read", invoice_id)
        if read is not None:
            p = read.payload
            number = f"{p['series']}-{p['number']}" if p.get("series") else str(p["number"])
            out["read"] = {
                "number": number,
                "supplier": _supplier_name(conn, p["supplier_afm"]),
                "total": format_eur(p["total_cents"]),
                "lines": len(p.get("lines") or []),
                "items": _items(p.get("lines") or []),
                "checks_ok": all((p.get("checks") or {}).values()),
            }
            if not all((p.get("checks") or {}).values()):
                bad = [k for k, v in p["checks"].items() if not v]
                out["issues"].append("Έλεγχοι που δεν πέρασαν: " + ", ".join(bad))
        dup = core._latest(conn, "invoice.duplicate_suspected", invoice_id)
        if dup is not None:
            out["issues"].append(describe(dup, names))
        mydata = core._latest(conn, "invoice.mydata_checked", invoice_id)
        if mydata is not None:
            out["mydata"] = describe(mydata, names)
        matched = core._latest(conn, "invoice.matched", invoice_id)
        if matched is not None:
            if not matched.payload.get("delivery_id"):
                out["issues"].append(describe(matched, names))
            for issue in matched.payload.get("issues") or []:
                note = issue.get("note") if isinstance(issue, dict) else None
                if note:
                    out["issues"].append(describe_text(note))
        iban = core._latest(conn, "supplier.new_iban_seen", invoice_id)
        if iban is not None:
            out["issues"].append(describe(iban, names))
        out["done"] = matched is not None or dup is not None
        return out
    finally:
        conn.close()


def describe_text(text):
    """A free note from the checks, with any IBAN cut down to its last four characters."""
    from xangx.screen.describe import _mask
    return _mask(str(text))
