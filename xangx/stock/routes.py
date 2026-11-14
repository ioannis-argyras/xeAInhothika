"""Pages and API of the stock role: the shelf camera phone page and its upload."""

import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from xangx.shared.db import connect, init_db
from xangx.stock import camera

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"
MAX_B64 = camera.MAX_PHOTO_BYTES * 4 // 3 + 16


@router.get("/stock/camera")
def camera_page():
    return FileResponse(STATIC / "camera.html", media_type="text/html")


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
        return camera.handle_photo(conn, camera_id, data)
    except camera.PhotoError as exc:
        raise HTTPException(400, str(exc)) from None
    except camera.CameraError as exc:
        raise HTTPException(502, f"Η κάμερα δεν πήρε απάντηση: {exc}") from None
    finally:
        conn.close()
