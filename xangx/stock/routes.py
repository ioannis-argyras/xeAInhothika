"""Pages and API of the stock role: shelf camera (photo upload) and delivery check (barcodes)."""

import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from xangx.shared.db import connect, init_db
from xangx.stock import camera, delivery, trends

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"
MAX_B64 = camera.MAX_PHOTO_BYTES * 4 // 3 + 16


# The phone must never keep an old page or stylesheet: it asks again every time (the file is small).
NO_STALE = {"Cache-Control": "no-cache"}


@router.get("/stock/camera")
def camera_page():
    return FileResponse(STATIC / "camera.html", media_type="text/html", headers=NO_STALE)


@router.get("/stock/trends/sales")
def sales_trends(until_id: int | None = None, days: int = 30):
    """Sales per Greek day, per hour (average) and the top 10 products. Read-only."""
    if not 1 <= days <= trends.MAX_DAYS:
        raise HTTPException(400, f"Οι μέρες πρέπει να είναι από 1 έως {trends.MAX_DAYS}.")
    conn = connect()
    try:
        init_db(conn)
        return trends.sales_trends(conn, until_id=until_id, days=days)
    finally:
        conn.close()


@router.get("/stock/style.css")
def stock_style():
    return FileResponse(STATIC / "stock.css", media_type="text/css; charset=utf-8", headers=NO_STALE)


@router.get("/stock/camera/shelf")
def camera_shelf(cam: str = ""):
    """The shelf this camera looks at, for the page title (e.g. «Ψυγείο 1»)."""
    conn = connect()
    try:
        init_db(conn)
        positions = camera.camera_positions(conn, cam)
    finally:
        conn.close()
    if not positions:
        raise HTTPException(404, "Δεν ξέρω αυτή την κάμερα. Άνοιξε τη σελίδα από τον σύνδεσμο της κάμερας.")
    return {"camera_id": cam, "name": positions[0]["name"].split(" · ")[0], "positions": len(positions)}


@router.post("/stock/camera/photo")
def camera_photo(body: dict = Body(...)):
    camera_id = body.get("camera_id") if isinstance(body, dict) else None
    image_b64 = body.get("image_b64") if isinstance(body, dict) else None
    if not isinstance(camera_id, str) or not camera_id or not isinstance(image_b64, str):
        raise HTTPException(400, "Χρειάζονται camera_id και image_b64.")
    if len(image_b64) > MAX_B64:
        raise HTTPException(400, "Η φωτογραφία πρέπει να είναι έως 5 MB.")
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[-1]
    try:
        data = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Η φωτογραφία δεν είναι σωστό base64.") from None
    conn = connect()
    try:
        init_db(conn)
        return camera.handle_photo(conn, camera_id, data, practice=body.get("practice") is True)
    except camera.PhotoError as exc:
        raise HTTPException(400, str(exc)) from None
    except camera.CameraError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        conn.close()


# --- Delivery check with barcodes (phone page /stock/scan) ---------------------

VENDOR = {"zxing-reader.js": "text/javascript; charset=utf-8", "zxing_reader.wasm": "application/wasm"}


@router.get("/stock/scan")
def scan_page():
    return FileResponse(STATIC / "scan.html", media_type="text/html", headers=NO_STALE)


@router.get("/stock/vendor/{name}")
def vendor_file(name: str):
    if name not in VENDOR:
        raise HTTPException(404)
    return FileResponse(STATIC / "vendor" / name, media_type=VENDOR[name],
                        headers={"Cache-Control": "public, max-age=86400"})


def _field(body, name):
    value = body.get(name) if isinstance(body, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, f"Χρειάζεται {name}.")
    return value.strip()


def _delivery_call(fn, *args):
    conn = connect()
    try:
        init_db(conn)
        return fn(conn, *args)
    except delivery.DeliveryError as exc:
        raise HTTPException(400, str(exc)) from None
    finally:
        conn.close()


@router.get("/stock/delivery/pending")
def delivery_pending():
    """The order the scan page opens when its link names none."""
    return {"order_id": _delivery_call(delivery.pending_order)}


@router.post("/stock/delivery/start")
def delivery_start(body: dict = Body(...)):
    return _delivery_call(delivery.start, _field(body, "order_id"))


@router.post("/stock/delivery/scan")
def delivery_scan(body: dict = Body(...)):
    return _delivery_call(delivery.scan, _field(body, "delivery_id"), _field(body, "barcode"))


@router.post("/stock/delivery/practice")
def delivery_practice(body: dict = Body(...)):
    codes = body.get("barcodes")
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
        raise HTTPException(400, "Χρειάζεται λίστα barcodes.")
    return _delivery_call(delivery.practice, _field(body, "order_id"), codes)


@router.post("/stock/delivery/complete")
def delivery_complete(body: dict = Body(...)):
    return _delivery_call(delivery.complete, _field(body, "delivery_id"))
