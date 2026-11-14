"""Director page for the owner's phone: /demo/skinothetis, /demo/steps, /demo/reset, /demo/step/{key}.

Behind the team access code like every other route (the access middleware wraps the whole app).
"""

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from xangx.demo import director
from xangx.shared.db import connect, init_db

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"
_busy = threading.Lock()  # one director action at a time (a reset in the middle of a step would tangle)
BUSY = "Το σύστημα δουλεύει ακόμα σε προηγούμενο κουμπί. Περίμενε λίγο."


def _with_db(action):
    if not _busy.acquire(blocking=False):
        return {"ok": False, "summary": BUSY, "busy": True}
    conn = connect()
    try:
        init_db(conn)
        return action(conn)
    finally:
        conn.close()
        _busy.release()


def _reset_sync():
    def action(conn):
        try:
            return dict(director.reset_demo(conn), ok=True)
        except Exception as exc:
            return dict(director._fail(exc))

    return _with_db(action)


def _step_sync(key):
    return _with_db(lambda conn: director.run_step(key, conn))


@router.get("/demo/skinothetis")
def director_page():
    return FileResponse(STATIC / "skinothetis.html", media_type="text/html")


@router.get("/demo/steps")
def director_steps():
    return [{"key": s["key"], "time": s["time"], "label": s["label"]} for s in director.STEPS]


@router.post("/demo/reset")
async def director_reset():
    return await run_in_threadpool(_reset_sync)


@router.post("/demo/step/{key}")
async def director_step(key: str):
    if key not in director.STEP_KEYS:
        raise HTTPException(404, "Δεν υπάρχει τέτοιο βήμα.")
    return await run_in_threadpool(_step_sync, key)
