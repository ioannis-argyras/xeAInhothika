"""Accountant pages and API.

GET /accountant/month?month=2026-11 : the month's folder (without month: this month).
GET /accountant/trends?days=30      : sales per day, prices, purchases (read only, JSON).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from xangx.accountant import month as month_mod
from xangx.accountant import trends as trends_mod
from xangx.invoices import core as invoices
from xangx.shared import connect, init_db

router = APIRouter()


@router.get("/accountant/month")
def month_folder(month: str = ""):
    if not month:  # the button on the invoices page: this month, Greek time
        from xangx.master.tools import greek_offset
        now = datetime.now(timezone.utc) + greek_offset(datetime.now(timezone.utc))
        month = now.strftime("%Y-%m")
    if not month_mod.valid_month(month):
        raise HTTPException(400, "Ο μήνας θέλει μορφή ΕΕΕΕ-ΜΜ, π.χ. 2026-11.")
    conn = connect()
    try:
        init_db(conn)
        _data, file_ref, _sha, _event = month_mod.build(conn, month)
    finally:
        conn.close()
    return FileResponse(invoices.data_dir() / file_ref, media_type="application/zip",
                        filename=f"fakelos-{month}.zip", headers={"Cache-Control": "no-store"})


@router.get("/accountant/trends")
def trends(days: int = trends_mod.DEFAULT_DAYS):
    conn = connect()
    try:
        init_db(conn)
        data = trends_mod.trends(conn, days)
    finally:
        conn.close()
    data["summary"] = trends_mod.summary_text(data)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})
