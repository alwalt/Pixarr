# app/api/routes/staging.py
# Staging routes only. Keep routes thin; reuse shared helpers/constants.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

# --- shared config/constants ---
from app.core.config import (
    STAGING_ROOTS,          # {"pc": Path(...), "icloud": Path(...), ...}
    SUPPORTED_EXT,          # extensions to show in list API
    IMAGE_EXT, VIDEO_EXT, RAW_EXT,  # classification sets for stats API
    ICLOUD,                 # icloud config section (lightweight settings)
    build_icloudpd_cmd,     # helper to construct icloudpd command from TOML
)

# --- shared helpers & schemas ---
from app.utils.http import safe_rel_under, abs_url
from app.utils.thumbs import serve_or_build_thumb
from app.schemas.media import StagingEntry, StagingStats
from app.services.metadata import read_metadata  # calls exiftool / Pillow

# --- sync subprocess (no queue, single-run guard) ---
import asyncio

# Router mounted under /api in main.py (→ /api/staging/...)
api_router = APIRouter(prefix="/staging", tags=["staging"])
# Public router mounted without prefix (→ /staging/* and /thumb/staging/*)
public_router = APIRouter(tags=["staging-public"])

# Single-run lock for icloud sync (prevents concurrent runs)
_icloud_sync_lock = asyncio.Lock()


# ---- small helper: resolve root name to a real base path ----
def resolve_staging_root(root: str) -> Path:
    """Map a root key (e.g. 'icloud') to its absolute directory or 404."""
    if root not in STAGING_ROOTS:
        raise HTTPException(status_code=404, detail=f"unknown staging root '{root}'")
    return STAGING_ROOTS[root].resolve()


# ===========================
# ========== API ============
# ===========================

@api_router.get("/roots", response_model=list[str])
def api_staging_roots() -> list[str]:
    """Return the available staging root keys that exist on disk."""
    roots: list[str] = []
    for k, p in STAGING_ROOTS.items():
        try:
            if p.exists() and p.is_dir():
                roots.append(k)
        except Exception:
            # Ignore unreadable/missing paths; we only show healthy roots.
            pass
    return sorted(roots)


@api_router.get("/list", response_model=list[StagingEntry])
def api_staging_list(request: Request, root: str, path: Optional[str] = "") -> list[StagingEntry]:
    """
    List immediate children (dirs + supported files) under a staging root/path.
    For files, include absolute media/thumbnail URLs.
    """
    base = resolve_staging_root(root)
    target_dir = (base / (path or ".")).resolve()

    # Security: prevent path traversal outside the root
    if safe_rel_under(base, target_dir) is None:
        raise HTTPException(status_code=403, detail="forbidden path")
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="directory not found")

    entries: list[StagingEntry] = []

    # Sort: directories first, then files by case-insensitive name
    for child in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = safe_rel_under(base, child)
        if rel is None:
            continue

        # Basic metadata (mtime can fail on races; keep robust)
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
            # Only include previewable media files
            if child.suffix.lower() not in SUPPORTED_EXT:
                continue
            rel_url = str(rel).replace("\\", "/")
            # Absolute URLs for the client
            media = abs_url(request, f"/staging/{root}/{rel_url}")
            thumb = abs_url(request, f"/thumb/staging/{root}/{rel_url}")
            entries.append(StagingEntry(
                name=child.name,
                rel_path=rel_url,
                is_dir=False,
                size=(st.st_size if st else None),
                mtime=mtime_iso,
                media_url=media,
                thumb_url=thumb,
            ))
    return entries


@api_router.get("/stats", response_model=StagingStats)
def api_staging_stats(root: str, path: Optional[str] = "") -> StagingStats:
    """
    Count items in the CURRENT directory (non-recursive), matching legacy behavior:
    - images/videos/raw by extension sets
    - other: files not matching the above
    - dirs: immediate subdirectories
    - total_files = images + videos + raw + other
      NOTE: RAW files increment both 'images' and 'raw'.
    """
    base = resolve_staging_root(root)
    target_dir = (base / (path or ".")).resolve()

    if safe_rel_under(base, target_dir) is None:
        raise HTTPException(status_code=403, detail="forbidden path")
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="directory not found")

    images = videos = raw = other = dirs = 0

    for child in target_dir.iterdir():
        try:
            if child.is_dir():
                dirs += 1
                continue
            ext = child.suffix.lower()

            # IMPORTANT: check RAW first so we can double-count as image+raw
            if ext in RAW_EXT:
                images += 1
                raw += 1
            elif ext in VIDEO_EXT:
                videos += 1
            elif ext in IMAGE_EXT:
                images += 1
            else:
                other += 1
        except Exception:
            # Permission/race issues → bucket as "other"
            other += 1

    total_files = images + videos + raw + other
    return StagingStats(
        images=images, videos=videos, raw=raw, other=other,
        dirs=dirs, total_files=total_files
    )


# ==============================
# ======== PUBLIC FILES ========
# ==============================

@public_router.get("/staging/{root}/{path:path}")
def get_staging_media(root: str, path: str):
    """Serve the original file from a staging root."""
    base = resolve_staging_root(root)
    abs_path = (base / path).resolve()
    if safe_rel_under(base, abs_path) is None:
        raise HTTPException(status_code=403, detail="forbidden path")
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(abs_path)


@public_router.get("/thumb/staging/{root}/{path:path}")
def get_staging_thumb(root: str, path: str, h: int = 220):
    """
    Serve (or build+cache) a JPEG thumbnail for a staging file at height=h.
    Falls back to the original on error (handled in serve_or_build_thumb).
    """
    base = resolve_staging_root(root)
    abs_path = (base / path).resolve()
    if safe_rel_under(base, abs_path) is None:
        raise HTTPException(status_code=403, detail="forbidden path")
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return serve_or_build_thumb(abs_path, h)


@api_router.get("/exif")
def api_staging_exif(root: str, path: str, compact: bool = True):
    """Return EXIF/QuickTime metadata for a staging file (flat dict for the UI)."""
    base = resolve_staging_root(root)
    abs_path = (base / path).resolve()

    if safe_rel_under(base, abs_path) is None:
        raise HTTPException(status_code=403, detail="forbidden path")
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    meta = read_metadata(abs_path, compact=compact)

    # Tag the area generically for the UI; still not a per-field mapper
    meta.setdefault("_area", "staging")
    meta.setdefault("_root", root)
    meta.setdefault("_rel_path", path)
    return meta


# ==============================
# ======= iCloud SYNC API ======
# ==============================

@api_router.post("/sync/icloud")
async def api_sync_icloud():
    """
    Kick off 'icloudpd' sync using config ([icloud] in pixarr.toml).
    - One-at-a-time via an asyncio.Lock
    - Returns stdout/stderr and exit code after completion
    """
    # Quick guard: allow the API to respond fast if a sync is in flight
    if _icloud_sync_lock.locked():
        return {"status": "busy", "msg": "icloud sync already running"}

    async with _icloud_sync_lock:
        # Validate config and build the command
        try:
            cmd = build_icloudpd_cmd()
        except ValueError as e:
            # Misconfiguration (disabled or no apple_id)
            return {"status": "error", "msg": str(e)}
        except Exception as e:
            return {"status": "error", "msg": f"failed to build command: {e}"}

        # Run without blocking the event loop; capture output
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return {
            "status": "done",
            "exit_code": proc.returncode,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
            # Optional: echo command for debugging (omit in prod if sensitive)
            "cmd": cmd,
        }
