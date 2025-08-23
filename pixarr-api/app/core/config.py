# app/core/config.py
# Loads Pixarr settings from a TOML file (defaults + overrides).
# - Reads PIXARR_CONFIG or falls back to app/pixarr.toml
# - Normalizes extension lists (lowercase, ensure leading dot)
# - SUPPORTED_EXT is derived from image ∪ video ∪ raw
# - Legacy stats rule: RAW extensions are also part of IMAGE_EXT
# - Exposes iCloud settings (ICLOUD) + a helper to build the icloudpd command

from __future__ import annotations
from pathlib import Path
import os
from typing import Dict, List, Optional
import tomli as tomllib  # py3.11+: tomllib in stdlib; using tomli for compatibility


# -------------------- Defaults (used if TOML omits keys) --------------------
_DEFAULTS = {
    "paths": {
        "data_dir": "/Volumes/Data/Pixarr/data",
        "review_subdir": "media/Review",
        "staging_subdir": "media/Staging",
        "thumb_subdir": "thumb-cache",
        # optional DB pieces; see "Database path" section below
        # "db_path": "/abs/or/relative.sqlite3"
        # "db_subdir": "db",
        # "db_file":   "app.sqlite3",
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
        # We IGNORE 'supported' and derive it from image|video|raw.
        "supported": ["jpg","jpeg","png","gif","webp","heic","heif","mp4","mov","webm","mkv","avi"],
        "image":     ["jpg","jpeg","png","tif","tiff","gif","webp","heic","heif","avif"],
        "video":     ["mp4","mov","m4v","avi","webm","mkv"],
        "raw":       ["dng","cr2","cr3","nef","arw","raf","rw2","orf","srw"],
    },
    # NEW: iCloud defaults (override via [icloud] in pixarr.toml)
    "icloud": {
        "enabled": False,
        "apple_id": "",
        "cookie_dir": "icloud-cookies",         # resolved under DATA_DIR if relative
        "staging_subdir": "media/Staging/icloud",
        "download_live_photos": True,           # False => add --skip-live-photos
        "download_videos": True,                # False => add --skip-videos
        "size": "original",                     # original|medium|thumb|adjusted|alternative
        "live_photo_size": "original",          # original|medium|thumb
        "recent": 0,                            # 0 = all; else map to --recent N
        "until_found": 0,                       # >0 => add --until-found N
    },
}


# -------------------- Read + merge TOML --------------------
# --- replace your _load_config_toml() with this smarter finder ---

from pathlib import Path
import os
import tomli as tomllib

def _find_config_path() -> Path | None:
    """Find pixarr.toml without user input.
    Priority:
      1) PIXARR_CONFIG
      2) ./pixarr.toml (CWD)
      3) ascend parents from CWD looking for pixarr.toml
      4) app/pixarr.toml (next to this file)
    """
    # 1) Explicit env
    cfg_env = os.getenv("PIXARR_CONFIG")
    if cfg_env:
        p = Path(cfg_env).expanduser()
        if p.exists():
            return p

    # 2) CWD
    cwd = Path.cwd()
    p = cwd / "pixarr.toml"
    if p.exists():
        return p

    # 3) Walk up to root looking for repo-root pixarr.toml
    cur = cwd
    while True:
        candidate = cur / "pixarr.toml"
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            break  # reached filesystem root
        cur = cur.parent

    # 4) app/pixarr.toml (sibling to this file)
    app_default = Path(__file__).with_name("pixarr.toml")
    if app_default.exists():
        return app_default

    return None


def _load_config_toml() -> dict:
    """Load TOML from best match path or return {} if not found."""
    path = _find_config_path()
    if path and path.exists():
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
# Merge + resolve the base directories used across the API
paths = {**_DEFAULTS["paths"], **_cfg.get("paths", {})}

DATA_DIR    = Path(paths["data_dir"]).expanduser().resolve()
REVIEW_DIR  = (DATA_DIR / paths["review_subdir"]).resolve()
STAGING_DIR = (DATA_DIR / paths["staging_subdir"]).resolve()
THUMB_DIR   = (DATA_DIR / paths["thumb_subdir"]).resolve()
THUMB_DIR.mkdir(parents=True, exist_ok=True)


# -------------------- Database path --------------------
# Supports either:
#   [paths] db_path   = "/absolute/or/relative/to/DATA_DIR/app.sqlite3"
# or
#   [paths] db_subdir = "db"
#   [paths] db_file   = "app.sqlite3"
paths = {**_DEFAULTS["paths"], **_cfg.get("paths", {})}  # (already merged above; repeated for clarity)

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
SUPPORTED_EXT = _image | _video | _raw

# 2) What the stats route uses:
#    Legacy behavior = RAW counted both as 'raw' and as 'images'.
IMAGE_EXT = _image | _raw
VIDEO_EXT = _video
RAW_EXT   = _raw


# -------------------- iCloud settings --------------------
class _IcloudSettings:
    """
    Lightweight container for iCloud importer settings.
    Paths are resolved relative to DATA_DIR when given as relative strings.
    """
    def __init__(self, cfg: dict, defaults: dict) -> None:
        c = {**defaults, **(cfg or {})}
        self.enabled: bool = bool(c.get("enabled", False))
        self.apple_id: str = str(c.get("apple_id", "")).strip()

        # resolve cookie_dir and staging_subdir under DATA_DIR if relative
        _cookie = Path(c.get("cookie_dir", defaults["cookie_dir"]))
        _staged = Path(c.get("staging_subdir", defaults["staging_subdir"]))
        self.cookie_dir: Path = _cookie if _cookie.is_absolute() else (DATA_DIR / _cookie)
        self.staging_subdir: Path = _staged if _staged.is_absolute() else (DATA_DIR / _staged)

        # toggles and sizes
        self.download_live_photos: bool = bool(c.get("download_live_photos", True))
        self.download_videos: bool      = bool(c.get("download_videos", True))
        self.size: str                  = str(c.get("size", "original"))
        self.live_photo_size: str       = str(c.get("live_photo_size", "original"))

        # throttling
        self.recent: int       = int(c.get("recent", 0))
        self.until_found: int  = int(c.get("until_found", 0))

    def __repr__(self) -> str:
        return (
            f"_IcloudSettings(enabled={self.enabled}, apple_id={'***' if self.apple_id else ''}, "
            f"cookie_dir={self.cookie_dir}, staging_subdir={self.staging_subdir}, "
            f"download_live_photos={self.download_live_photos}, download_videos={self.download_videos}, "
            f"size={self.size}, live_photo_size={self.live_photo_size}, "
            f"recent={self.recent}, until_found={self.until_found})"
        )


# Merge user config with defaults and expose as ICLOUD
ICLOUD = _IcloudSettings(_cfg.get("icloud", {}), _DEFAULTS["icloud"])


def build_icloudpd_cmd() -> List[str]:
    """
    Build the 'icloudpd' command from ICLOUD settings.
    Returns a list suitable for subprocess / asyncio.create_subprocess_exec.
    Raises ValueError if iCloud sync is disabled or apple_id is missing.

    Example return:
    [
      'icloudpd', '--username', 'walt.alvarado@me.com',
      '--cookie-directory', '/…/data/icloud-cookies',
      '-d', '/…/data/media/Staging/icloud',
      '--recent', '5',
      '--size', 'original', '--live-photo-size', 'original'
    ]
    """
    if not ICLOUD.enabled:
        raise ValueError("iCloud sync is disabled in config ([icloud].enabled = false).")
    if not ICLOUD.apple_id:
        raise ValueError("iCloud apple_id is not set in config ([icloud].apple_id).")

    cmd: List[str] = [
        "icloudpd",
        "--username", ICLOUD.apple_id,
        "--cookie-directory", str(ICLOUD.cookie_dir),
        "-d", str(ICLOUD.staging_subdir),
    ]

    # Optional throttling
    if ICLOUD.recent and ICLOUD.recent > 0:
        cmd += ["--recent", str(ICLOUD.recent)]
    if ICLOUD.until_found and ICLOUD.until_found > 0:
        cmd += ["--until-found", str(ICLOUD.until_found)]

    # Feature toggles (negated flags in icloudpd)
    if not ICLOUD.download_live_photos:
        cmd.append("--skip-live-photos")
    if not ICLOUD.download_videos:
        cmd.append("--skip-videos")

    # Sizes
    if ICLOUD.size:
        cmd += ["--size", ICLOUD.size]
    if ICLOUD.live_photo_size:
        cmd += ["--live-photo-size", ICLOUD.live_photo_size]

    return cmd


# -------------------- Optional: HEIC opener --------------------
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pass
