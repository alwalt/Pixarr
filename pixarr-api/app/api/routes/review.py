# app/api/routes/review.py
# Endpoints related to Review library:
# - GET /api/review
# - GET /media/{path}
# - GET /thumb/review/{path}
from datetime import datetime
from pathlib import Path
import urllib.parse
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.core.config import REVIEW_DIR
from app.repositories.db import get_conn
from app.schemas.media import MediaItem
from app.utils.http import abs_url, safe_rel_under
from app.utils.thumbs import serve_or_build_thumb

api_router = APIRouter(tags=["review"])     # mounted under /api in main
public_router = APIRouter()                 # mounted without prefix in main

def _row_to_media_item(request: Request, row: sqlite3.Row) -> Optional[MediaItem]:
    canonical = Path(row["canonical_path"])
    rel = safe_rel_under(REVIEW_DIR, canonical)
    if rel is None:
        return None
    rel_url = urllib.parse.quote(str(rel).replace("\\", "/"))
    media = abs_url(request, f"/media/{rel_url}")
    thumb = abs_url(request, f"/thumb/review/{rel_url}")
    return MediaItem(
        id=row["id"],
        canonical_path=row["canonical_path"],
        taken_at=row["taken_at"],
        gps_lat=row["gps_lat"],
        gps_lon=row["gps_lon"],
        media_url=media,
        thumb_url=thumb,
    )

@api_router.get("/review", response_model=List[MediaItem])
def list_review(request: Request, limit: int = 200, offset: int = 0, q: Optional[str] = None):
    # validate paging params
    if not (1 <= limit <= 1000):
        raise HTTPException(400, "limit must be 1..1000")
    if offset < 0:
        raise HTTPException(400, "offset must be >= 0")

    sql = """
      SELECT id, canonical_path, taken_at, gps_lat, gps_lon
      FROM media
      WHERE state='review'
    """
    params: list = []
    if q:
        sql += " AND (id LIKE ? OR canonical_path LIKE ?)"
        like = f"%{q}%"; params += [like, like]
    sql += " ORDER BY taken_at IS NULL, taken_at DESC, id ASC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_conn() as con:
        rows = con.execute(sql, params).fetchall()

    out: list[MediaItem] = []
    for r in rows:
        item = _row_to_media_item(request, r)
        if item:
            out.append(item)
    return out

@public_router.get("/media/{path:path}")
def get_review_media(path: str):
    abs_path = (REVIEW_DIR / path).resolve()
    if safe_rel_under(REVIEW_DIR, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(abs_path)

@public_router.get("/thumb/review/{path:path}")
def get_review_thumb(path: str, h: int = 220):
    abs_path = (REVIEW_DIR / path).resolve()
    if safe_rel_under(REVIEW_DIR, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")
    return serve_or_build_thumb(abs_path, h)
