# app/core/config.py
# Loads Pixarr settings from a TOML file (defaults + overrides).
# - Reads PIXARR_CONFIG or falls back to app/pixarr.toml
# - Normalizes extension lists (lowercase, ensure leading dot)
# - SUPPORTED_EXT is derived from image ∪ video ∪ raw (your request)
# - Legacy stats rule: RAW extensions are also part of IMAGE_EXT

from __future__ import annotations
from pathlib import Path
import os
import tomli as tomllib 

# -------------------- Defaults (used if TOML omits keys) --------------------
_DEFAULTS = {
    "paths": {
        "data_dir": "/Volumes/Data/Pixarr/data",
        "review_subdir": "media/Review",
        "staging_subdir": "media/Staging",
        "thumb_subdir": "thumb-cache",
    },
    "staging": {
        "roots": {
            "pc": "pc",
            "icloud": "icloud",
            "sdcard": "sdcard",
            "other": "other",
        }
    },
    "ext": {
        # Kept for completeness; we IGNORE 'supported' and derive it from image|video|raw.
        "supported": ["jpg","jpeg","png","gif","webp","heic","heif","mp4","mov","webm","mkv","avi"],
        "image":     ["jpg","jpeg","png","tif","tiff","gif","webp","heic","heif","avif"],
        "video":     ["mp4","mov","m4v","avi","webm","mkv"],
        "raw":       ["dng","cr2","cr3","nef","arw","raf","rw2","orf","srw"],
    },
}

# -------------------- Read + merge TOML --------------------
def _load_config_toml() -> dict:
    """
    Load TOML from PIXARR_CONFIG (if set) or app/pixarr.toml.
    Returns {} if no file found.
    """
    cfg_path = os.getenv("PIXARR_CONFIG")
    path = Path(cfg_path) if cfg_path else Path(__file__).with_name("pixarr.toml")
    if path.exists():
        with path.open("rb") as f:
            return tomllib.load(f)
    return {}

def _norm_ext_list(exts: list[str]) -> set[str]:
    """
    Normalize extension strings: ensure leading dot and lowercase.
    Accepts 'jpg' or '.jpg' and returns '.jpg'.
    """
    out: set[str] = set()
    for e in exts:
        e = (e or "").strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.add(e)
    return out

_cfg = _load_config_toml()

# -------------------- Paths --------------------
paths = {**_DEFAULTS["paths"], **_cfg.get("paths", {})}

DATA_DIR    = Path(paths["data_dir"])
REVIEW_DIR  = (DATA_DIR / paths["review_subdir"]).resolve()
STAGING_DIR = (DATA_DIR / paths["staging_subdir"]).resolve()
THUMB_DIR   = (DATA_DIR / paths["thumb_subdir"]).resolve()
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# ---------------- Database path --------------------
# Supports either:
#   [paths] db_path   = "/absolute/or/relative/to/DATA_DIR/app.sqlite3"
# or
#   [paths] db_subdir = "db"
#   [paths] db_file   = "app.sqlite3"
paths = {**_DEFAULTS["paths"], **_cfg.get("paths", {})}  # you already have this above

db_path_cfg = paths.get("db_path")
if db_path_cfg:
    p = Path(db_path_cfg)
    DB_PATH = p if p.is_absolute() else (DATA_DIR / p)
else:
    db_subdir = paths.get("db_subdir", "db")
    db_file   = paths.get("db_file", "app.sqlite3")
    DB_PATH   = (DATA_DIR / db_subdir / db_file)

DB_PATH = DB_PATH.resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# -------------------- Staging roots --------------------
# Values in TOML can be absolute or relative to STAGING_DIR.
roots_cfg: dict = _cfg.get("staging", {}).get("roots", {})
roots_merged = {**_DEFAULTS["staging"]["roots"], **roots_cfg}

from typing import Dict
STAGING_ROOTS: Dict[str, Path] = {}
for name, sub in roots_merged.items():
    p = Path(sub)
    STAGING_ROOTS[name] = p if p.is_absolute() else (STAGING_DIR / p)

# -------------------- Extensions --------------------
# Merge ext config and normalize each set.
ext_cfg = {**_DEFAULTS["ext"], **_cfg.get("ext", {})}

_image = _norm_ext_list(list(ext_cfg.get("image", [])))
_video = _norm_ext_list(list(ext_cfg.get("video", [])))
_raw   = _norm_ext_list(list(ext_cfg.get("raw",   [])))

# 1) What the listing API / UI should show:
#    You asked to include RAW, so we derive SUPPORTED from image ∪ video ∪ raw.
SUPPORTED_EXT = _image | _video | _raw

# 2) What the stats route uses:
#    Legacy behavior = RAW counted both as 'raw' and as 'images'.
IMAGE_EXT = _image | _raw
VIDEO_EXT = _video
RAW_EXT   = _raw

# -------------------- Optional: HEIC opener --------------------
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass
