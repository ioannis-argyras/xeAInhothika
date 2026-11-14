"""Data for the shelf page (pages/rafi.html): positions with today's moves, and the shelf photos.

Only reads events and reference tables. The router is joined to the app in a later step:
    from xangx.screen.api_b2 import router as shelf_router; app.include_router(shelf_router)
"""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from xangx.master.tools import greek_day_start_utc, greek_offset
from xangx.screen.describe import load_names
from xangx.shared import views
from xangx.shared.db import connect, init_db
from xangx.shared.events import read_events
from xangx.stock import camera

router = APIRouter()

# The only files the photo route serves: <sha256>.<ext> that camera.py wrote. Nothing else.
PHOTO_NAME = re.compile(r"[0-9a-f]{64}\.(jpg|jpeg|png|webp)")
PHOTO_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MOVE_TYPES = {"shelf.items_taken": "taken", "shelf.items_added": "added"}


def _parse_utc(at):
    return datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _local(at, seconds=False):
    moment = _parse_utc(at)
    return (moment + greek_offset(moment)).strftime("%H:%M:%S" if seconds else "%H:%M")


def _reference_time(conn, until_id):
    """"Now": the real clock, or the last event up to `until_id` (time machine)."""
    if until_id is not None:
        row = conn.execute("SELECT at FROM events WHERE id <= ? ORDER BY id DESC LIMIT 1",
                           (until_id,)).fetchone()
        return _parse_utc(row["at"]) if row else datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def _photo_url(ref):
    """URL of a shelf photo, or None when the name is not a stored photo or the file is gone."""
    if not isinstance(ref, str) or not PHOTO_NAME.fullmatch(ref):
        return None
    if not (camera.files_dir() / ref).is_file():
        return None
    return f"/api/screen/photo/{ref}"


def _move(e):
    p = e.payload
    kind = MOVE_TYPES[e.type]
    return {"id": e.id, "kind": kind, "at": _local(e.at), "at_full": _local(e.at, seconds=True),
            "qty": p.get("taken" if kind == "taken" else "added"),
            "confidence": p.get("confidence"),
            "file_ref": p.get("file_ref"), "prev_file_ref": p.get("prev_file_ref")}


def build_shelves(conn, until_id=None):
    names = load_names(conn)
    day_start = greek_day_start_utc(_reference_time(conn, until_id))
    levels = views.stock_levels(conn, until_id)

    moves, low_today, photo_at = {}, {}, {}
    for e in read_events(conn, until_id=until_id,
                         types=[*MOVE_TYPES, "stock.low_detected"]):
        if e.type == "stock.low_detected":
            if e.at >= day_start:
                low_today[e.payload.get("product_id")] = e.payload.get("on_hand")
            continue
        photo_at.setdefault(e.payload.get("file_ref"), e.at)
        if e.at >= day_start:
            moves.setdefault(e.payload.get("position_id"), []).append(e)

    thresholds = {r["id"]: r["low_threshold"] for r in conn.execute("SELECT id, low_threshold FROM products")}
    rows = conn.execute("SELECT id, name, product_id, camera_id FROM positions ORDER BY id").fetchall()
    slot_of = {}
    for r in rows:
        slot_of[r["id"]] = sum(1 for o in rows if o["camera_id"] == r["camera_id"] and o["id"] <= r["id"])

    positions = []
    for r in rows:
        pid = r["product_id"]
        threshold = thresholds.get(pid)
        stock = levels.get(pid, 0) if pid else None
        shown = low_today[pid] if pid in low_today else stock
        low = bool(pid) and (pid in low_today or (
            threshold is not None and stock is not None and stock <= threshold))
        mine = list(reversed(moves.get(r["id"], [])))  # newest first
        moves_out = [_move(e) for e in mine]
        positions.append({
            "position_id": r["id"], "name": r["name"], "slot": slot_of[r["id"]],
            "camera_id": r["camera_id"], "product_id": pid,
            "product_name": names["product"].get(pid) or pid or r["name"],
            "on_hand": shown, "low_threshold": threshold, "low": low,
            "taken_today": sum(m["qty"] or 0 for m in moves_out if m["kind"] == "taken"),
            "added_today": sum(m["qty"] or 0 for m in moves_out if m["kind"] == "added"),
            "moves": moves_out, "last": moves_out[0] if moves_out else None,
        })
    positions.sort(key=lambda p: (not p["low"], -(p["last"]["id"] if p["last"] else 0),
                                  p["camera_id"] or "", p["position_id"]))

    latest = None
    moved = [p for p in positions if p["last"]]
    if moved:
        p = max(moved, key=lambda x: x["last"]["id"])
        m = p["last"]
        taken = m["qty"] if m["kind"] == "taken" else 0
        latest = {
            "position_id": p["position_id"], "position_name": p["name"],
            "product_name": p["product_name"], "camera_id": p["camera_id"], "low": p["low"],
            "kind": m["kind"], "qty": m["qty"], "at": m["at"], "at_full": m["at_full"],
            "confidence": m["confidence"], "left": p["on_hand"],
            "before": (p["on_hand"] + taken) if p["on_hand"] is not None else None,
            "after_url": _photo_url(m["file_ref"]), "before_url": _photo_url(m["prev_file_ref"]),
            "before_at_full": _local(photo_at[m["prev_file_ref"]], seconds=True)
            if m["prev_file_ref"] in photo_at else None,
        }

    cameras = [p["camera_id"] for p in positions if p["camera_id"]]
    last_id = conn.execute("SELECT COALESCE(MAX(id),0) AS m FROM events"
                           + (" WHERE id <= ?" if until_id is not None else ""),
                           (until_id,) if until_id is not None else ()).fetchone()["m"]
    return {"last_id": last_id, "low_count": sum(1 for p in positions if p["low"]),
            "total": len(positions), "latest": latest, "positions": positions,
            "camera_id": (latest or {}).get("camera_id") or (sorted(cameras)[0] if cameras else None)}


@router.get("/api/screen/shelves")
def shelves(until_id: int | None = None):
    conn = connect()
    try:
        init_db(conn)
        return build_shelves(conn, until_id)
    finally:
        conn.close()


@router.get("/api/screen/photo/{name}")
def photo(name: str):
    match = PHOTO_NAME.fullmatch(name)
    if not match:
        raise HTTPException(404)
    path = camera.files_dir() / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=PHOTO_TYPES[match.group(1)],
                        headers={"Cache-Control": "private, max-age=3600",
                                 "X-Content-Type-Options": "nosniff"})
