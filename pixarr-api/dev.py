# main.py — FastAPI backend for Pixarr UI (Review + Staging + Thumbs)
# ---------------------------------------------------------------
# What you get:
# - GET /api/review?limit=&offset=&q=      → list Review media (with media_url + thumb_url)
# - GET /media/{path}                       → serve Review originals
# - GET /api/staging/roots                  → list available staging roots
# - GET /api/staging/list?root=&path=       → list dirs/files under a staging root (files include media_url + thumb_url)
# - GET /staging/{root}/{path}              → serve Staging originals
# - GET /thumb/review/{path}?h=220          → serve/caches Review thumbnails
# - GET /thumb/staging/{root}/{path}?h=220  → serve/caches Staging thumbnails
#
# How to run:
#   python -m venv .venv && source .venv/bin/activate
#   pip install fastapi uvicorn[standard] pydantic pillow pillow-heif
#   uvicorn main:app --reload --port 8000

from __future__ import annotations
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
from datetime import datetime
import sqlite3
import urllib.parse
import hashlib
import io

# Pillow + (optional) HEIC support
from PIL import Image
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass

# ---------- CONFIG: point at your Pixarr data dir ----------
DATA_DIR = Path("/Volumes/Data/Pixarr/data")  # <-- CHANGE THIS if needed
DB_PATH = DATA_DIR / "db" / "app.sqlite3"

REVIEW_DIR = DATA_DIR / "media" / "Review"
STAGING_DIR = DATA_DIR / "media" / "Staging"

# Pick the staging roots you actually use:
STAGING_ROOTS = {
    "pc": STAGING_DIR / "pc",
    "icloud": STAGING_DIR / "icloud",
    "sdcard": STAGING_DIR / "sdcard",
    "other": STAGING_DIR / "other",
}

# What counts as a previewable media candidate in Staging
SUPPORTED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".heic", ".heif",
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
}

# ---------- Classification sets for stats ----------
# These match your ingest script (plus a couple video/webm that you already allow).
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp", ".heic", ".heif", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}
RAW_EXT   = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2", ".orf", ".srw"}

# Thumbnail cache dir
THUMB_DIR = DATA_DIR / "thumb-cache"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# ---------- App + CORS ----------
app = FastAPI(title="Pixarr API", version="0.2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Pydantic models ----------
class MediaItem(BaseModel):
    id: str
    canonical_path: str
    taken_at: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    media_url: str                  # absolute URL to original
    thumb_url: Optional[str] = None # absolute URL to thumb

class StagingEntry(BaseModel):
    name: str
    rel_path: str
    is_dir: bool
    size: Optional[int] = None
    mtime: Optional[str] = None
    media_url: Optional[str] = None
    thumb_url: Optional[str] = None

# NEW: stats response model
class StagingStats(BaseModel):
    images: int
    videos: int
    raw: int
    other: int
    dirs: int
    total_files: int

# ---------- Helpers ----------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def safe_rel_under(base: Path, target: Path) -> Optional[Path]:
    try:
        return target.resolve().relative_to(base.resolve())
    except Exception:
        return None

def abs_url(request: Request, path: str) -> str:
    # return absolute URL (scheme://host/path)
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"

def thumb_key(abs_path: Path, h: int) -> Path:
    key = hashlib.sha1(f"{abs_path}|h={h}".encode()).hexdigest()
    return THUMB_DIR / f"{key}.jpg"

def make_thumb(abs_path: Path, h: int) -> bytes:
    with Image.open(abs_path) as im:
        im = im.convert("RGB")
        w, hh = im.size
        if hh == 0:
            scale = 1.0
        else:
            scale = h / hh
        new_w = max(int(w * scale), 1)
        im = im.resize((new_w, h))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue()

# ---------- Review ----------
def row_to_media_item(request: Request, row: sqlite3.Row) -> Optional[MediaItem]:
    # row["canonical_path"] should be an absolute path under REVIEW_DIR
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

@app.get("/api/review", response_model=List[MediaItem])
def api_review(request: Request, limit: int = 200, offset: int = 0, q: Optional[str] = None):
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
        like = f"%{q}%"
        params += [like, like]
    sql += " ORDER BY taken_at IS NULL, taken_at DESC, id ASC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_conn() as con:
        rows = con.execute(sql, params).fetchall()

    out: list[MediaItem] = []
    for r in rows:
        item = row_to_media_item(request, r)
        if item:
            out.append(item)
    return out

@app.get("/media/{path:path}")
def get_review_media(path: str):
    abs_path = (REVIEW_DIR / path).resolve()
    if safe_rel_under(REVIEW_DIR, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(abs_path)

@app.get("/thumb/review/{path:path}")
def get_review_thumb(path: str, h: int = 220):
    abs_path = (REVIEW_DIR / path).resolve()
    if safe_rel_under(REVIEW_DIR, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")

    cache_path = thumb_key(abs_path, h)
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/jpeg")

    try:
        img_bytes = make_thumb(abs_path, h)
        cache_path.write_bytes(img_bytes)
        return Response(img_bytes, media_type="image/jpeg")
    except Exception:
        # fall back to original
        return FileResponse(abs_path)

# ---------- Staging ----------
def resolve_staging_root(root: str) -> Path:
    if root not in STAGING_ROOTS:
        raise HTTPException(404, f"unknown staging root '{root}'")
    return STAGING_ROOTS[root].resolve()

@app.get("/api/staging/roots", response_model=List[str])
def api_staging_roots():
    roots: list[str] = []
    for k, p in STAGING_ROOTS.items():
        try:
            if p.exists() and p.is_dir():
                roots.append(k)
        except Exception:
            pass
    return sorted(roots)

@app.get("/api/staging/list", response_model=List[StagingEntry])
def api_staging_list(request: Request, root: str, path: Optional[str] = ""):
    base = resolve_staging_root(root)
    target_dir = (base / (path or ".")).resolve()
    if safe_rel_under(base, target_dir) is None:
        raise HTTPException(403, "forbidden path")
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "directory not found")

    entries: list[StagingEntry] = []
    for child in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = safe_rel_under(base, child)
        if rel is None:
            continue

        try:
            st = child.stat()
            mtime_iso = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        except Exception:
            st = None
            mtime_iso = None

        if child.is_dir():
            entries.append(StagingEntry(
                name=child.name,
                rel_path=str(rel).replace("\\", "/"),
                is_dir=True,
                size=None,
                mtime=mtime_iso,
            ))
        else:
            if child.suffix.lower() not in SUPPORTED_EXT:
                continue
            rel_url = urllib.parse.quote(str(rel).replace("\\", "/"))
            media = abs_url(request, f"/staging/{root}/{rel_url}")
            thumb = abs_url(request, f"/thumb/staging/{root}/{rel_url}")
            entries.append(StagingEntry(
                name=child.name,
                rel_path=str(rel).replace("\\", "/"),
                is_dir=False,
                size=(st.st_size if st else None),
                mtime=mtime_iso,
                media_url=media,
                thumb_url=thumb,
            ))
    return entries

# NEW: stats endpoint that counts everything in the folder, including junk/other
@app.get("/api/staging/stats", response_model=StagingStats)
def api_staging_stats(root: str, path: Optional[str] = ""):
    """
    Return counts for the current directory:
    - images/videos/raw based on extension sets
    - other = files not matching any above
    - dirs
    - total_files = images + videos + raw + other
    """
    base = resolve_staging_root(root)
    target_dir = (base / (path or ".")).resolve()
    if safe_rel_under(base, target_dir) is None:
        raise HTTPException(403, "forbidden path")
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(404, "directory not found")

    images = videos = raw = other = dirs = 0

    for child in target_dir.iterdir():
        try:
            if child.is_dir():
                dirs += 1
                continue
            ext = child.suffix.lower()
            if ext in IMAGE_EXT:
                images += 1
            elif ext in VIDEO_EXT:
                videos += 1
            elif ext in RAW_EXT:
                images += 1
                raw += 1
            else:
                other += 1
        except Exception:
            # In case of permission or race errors, bucket as "other"
            other += 1

    total_files = images + videos + raw + other
    return StagingStats(
        images=images, videos=videos, raw=raw, other=other,
        dirs=dirs, total_files=total_files
    )

@app.get("/staging/{root}/{path:path}")
def get_staging_media(root: str, path: str):
    base = resolve_staging_root(root)
    abs_path = (base / path).resolve()
    if safe_rel_under(base, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(abs_path)

@app.get("/thumb/staging/{root}/{path:path}")
def get_staging_thumb(root: str, path: str, h: int = 220):
    base = resolve_staging_root(root)
    abs_path = (base / path).resolve()
    if safe_rel_under(base, abs_path) is None:
        raise HTTPException(403, "forbidden path")
    if not abs_path.is_file():
        raise HTTPException(404, "file not found")

    cache_path = thumb_key(abs_path, h)
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/jpeg")

    try:
        img_bytes = make_thumb(abs_path, h)
        cache_path.write_bytes(img_bytes)
        return Response(img_bytes, media_type="image/jpeg")
    except Exception:
        return FileResponse(abs_path)
