#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pixarr — ingest pass over Staging subfolders.

Highlights:
- EXIF/QuickTime first; optional filename-derived date fallback (--allow-filename-dates)
- Optional file-date fallback (--allow-file-dates) if you really want ModifyDate/FileModifyDate
- Quarantine policy is driven by pixarr.toml ([quarantine] section)
- Dry-run by default; use --write to actually move files to Review/
- DB auto-initializes from db/schema.sql the first time

Requirements:
- Python 3.9+ (uses tomli on ≤3.10)
- exiftool on PATH
- Pillow for content hashing of images (optional plugins: pillow-heif for HEIC, pillow-avif-plugin for AVIF)  # [CONTENT HASH]
"""

import os
import sys
import uuid
import json
import sqlite3
import hashlib
import subprocess
import time
import argparse
import tomli as toml   # you installed this; 3.11+ would use tomllib
import shutil
import re
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict
from collections import defaultdict, Counter

# --- optional image decoders for content hashing ---------------------------------------  # [CONTENT HASH]
try:
    from PIL import Image, ImageOps  # Pillow
except Exception:
    Image = None
    ImageOps = None

try:
    # Enables Pillow to open HEIC/HEIF if installed
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:
    pillow_heif = None

# ---------- Global toggles ----------
DRY_RUN = True  # overridden by --write

# Defaults used only if a list is missing in config
DEFAULT_IMAGES = [
    "jpg", "jpeg", "png", "tif", "tiff", "gif", "webp", "heic", "heif", "avif"
]
DEFAULT_RAW = [
    "dng", "cr2", "cr3", "nef", "arw", "raf", "rw2", "orf", "srw"
]
DEFAULT_VIDEOS = [
    "mp4", "mov", "m4v", "avi", "webm", "mkv"
]

def _norm_ext_list(items, *, default):
    """
    Normalize a list of extensions:
      - strip whitespace, lowercase
      - ensure a leading dot ('.jpg')
      - drop empties
    Returns a set like {'.jpg', '.png'}
    """
    source = items if items is not None else default
    out: set[str] = set()
    for s in source:
        if not s:
            continue
        s = str(s).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.add(s)
    return out

# ----------------------------------------------------------------------------------------
# Formats from config
# ----------------------------------------------------------------------------------------

def _formats_from_cfg_dict(cfg: dict) -> tuple[set[str], set[str], set[str]]:
    """
    Normalize formats from an in-memory config dict.
    Expected structure:
      [formats]
      images = ["jpg", "png", ...]
      raw    = ["dng", "cr3", ...]
      videos = ["mp4", "mov", ...]
    Falls back to DEFAULT_* if keys/section are missing.
    """
    fmts = (cfg.get("formats") or {})
    images = _norm_ext_list(fmts.get("images"), default=DEFAULT_IMAGES)
    raw    = _norm_ext_list(fmts.get("raw"),    default=DEFAULT_RAW)
    videos = _norm_ext_list(fmts.get("videos"), default=DEFAULT_VIDEOS)
    return images, raw, videos

def _load_formats_from_config(config_path: Path) -> tuple[set[str], set[str], set[str]]:
    """
    Load formats via the shared load_config() helper (single source of truth).
    Returns sets with leading dots.
    """
    cfg = load_config(config_path)  # {} on missing/parse error
    return _formats_from_cfg_dict(cfg)

# -------------------------------------------------------------------------------------------------
# Note: we initialize formats to defaults at import time (safe).
#       In main() we override them from pixarr.toml to keep a single source of truth.
# -------------------------------------------------------------------------------------------------
IMAGE_EXT: set[str]  = _norm_ext_list(DEFAULT_IMAGES,  default=DEFAULT_IMAGES)  # non-RAW images (for pixel hash)
RAW_EXT: set[str]    = _norm_ext_list(DEFAULT_RAW,     default=DEFAULT_RAW)     # RAW (counted as "pictures"; not thumbed/hashed)
VIDEO_EXT: set[str]  = _norm_ext_list(DEFAULT_VIDEOS,  default=DEFAULT_VIDEOS)
SUPPORTED_EXT: set[str] = IMAGE_EXT | RAW_EXT | VIDEO_EXT   # everything we accept during ingest

GENERIC_FOLDERS = {"dcim", "misc", "export", "photos", "images", "img", "camera", "mobile", "iphone", "android"}

JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_PREFIXES = {"._"}  # AppleDouble resource forks like ._IMG_1234.JPG
DIR_IGNORE = {".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems"}

# Fallback toggle set by CLI
ALLOW_FILENAME_DATES = False

# Paths configured at runtime
DATA_DIR: Path
DB_PATH: Path
REVIEW_ROOT: Path
QUARANTINE_ROOT: Path
STAGING_SOURCES: Dict[str, Path]
SCHEMA_PATH: Path

# Runtime quarantine policy (from pixarr.toml)
QUAR: Dict[str, bool] = {}

# exiftool (checked at runtime)
EXIFTOOL_PATH = shutil.which("exiftool")

# --- Duplicate quarantine subdir mapping ------------------------------------------------  # [DUPES]
# We keep DB reasons distinct (in_library vs in_review) but use ONE folder on disk.
REASON_TO_SUBDIR = {
    "duplicate_in_library": "duplicate",
    "duplicate_in_review": "duplicate",
    # everything else maps to its own reason name
}

# ---------- Utilities ----------

def ensure_exiftool() -> None:
    if not EXIFTOOL_PATH:
        sys.stderr.write("FATAL: exiftool not found on PATH. Install it (e.g., brew install exiftool).\n")
        sys.exit(1)

def repo_root() -> Path:
    """Resolve repo root as folder containing this file's parent (Pixarr/)."""
    return Path(__file__).resolve().parents[1]

LOGGER = logging.getLogger("pixarr")

def batch_logger(ingest_id: str, source: str) -> logging.LoggerAdapter:
    """Attach ingest_id + source to every log record in this batch."""
    return logging.LoggerAdapter(LOGGER, {"ingest_id": ingest_id, "source": source})

def log(msg: str, level: int = logging.INFO) -> None:
    LOGGER.log(level, msg)

def ensure_dirs() -> None:
    """Create core dirs; quarantine reason subfolders are created lazily."""
    (DATA_DIR / "db").mkdir(parents=True, exist_ok=True)
    for d in [
        "Staging/pc", "Staging/other", "Staging/icloud", "Staging/sdcard",
        "Review", "Library", "Quarantine",
        "Quarantine/duplicate",  # pre-create the unified duplicate folder     # [DUPES]
    ]:
        (DATA_DIR / "media" / d).mkdir(parents=True, exist_ok=True)

def ensure_db() -> None:
    """Create app.sqlite3 from schema.sql if missing; apply core PRAGMAs."""
    ensure_dirs()
    if not DB_PATH.exists():
        log(f"Initializing database at {DB_PATH} …")
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            PRAGMA temp_store=MEMORY;
        """)
        conn.close()

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def pathize(base: Path) -> None:
    """Derive all runtime paths from DATA_DIR and set globals."""
    global DATA_DIR, DB_PATH, REVIEW_ROOT, QUARANTINE_ROOT, STAGING_SOURCES, SCHEMA_PATH
    DATA_DIR = base
    DB_PATH = DATA_DIR / "db" / "app.sqlite3"
    REVIEW_ROOT = DATA_DIR / "media" / "Review"
    QUARANTINE_ROOT = DATA_DIR / "media" / "Quarantine"
    STAGING_BASE = DATA_DIR / "media" / "Staging"
    STAGING_SOURCES = {
        "Staging/pc":     STAGING_BASE / "pc",
        "Staging/other":  STAGING_BASE / "other",
        "Staging/icloud": STAGING_BASE / "icloud",
        "Staging/sdcard": STAGING_BASE / "sdcard",
    }
    SCHEMA_PATH = repo_root() / "db" / "schema.sql"

def resolve_source_tokens(tokens: list[str]) -> list[tuple[str, Path]]:
    """
    Turn CLI tokens into (label, path) pairs.
    Accepts:
      - 'pc' | 'other' | 'icloud' | 'sdcard'
      - 'Staging/pc' etc.
      - Subpaths like 'other/trip1' (relative to data/media/Staging)
      - Absolute/relative paths (treated as custom roots)
    """
    if not tokens:
        return list(STAGING_SOURCES.items())

    out, seen = [], set()
    staging_base = DATA_DIR / "media" / "Staging"

    for tok in tokens:
        tok_stripped = tok.strip()

        if tok_stripped in ("pc", "other", "icloud", "sdcard"):
            label = f"Staging/{tok_stripped}"
            path = STAGING_SOURCES[label]

        elif tok_stripped in STAGING_SOURCES:
            label = tok_stripped
            path = STAGING_SOURCES[label]

        elif "/" in tok_stripped and not tok_stripped.startswith("/"):
            # subdir under Staging, e.g. 'other/trip1'
            path = (staging_base / tok_stripped).resolve()
            label = f"Staging/{tok_stripped}"

        else:
            # absolute or relative filesystem path
            p = Path(tok_stripped).expanduser()
            if not p.is_absolute():
                p = (staging_base / tok_stripped).resolve()
            label = f"Custom:{tok_stripped}"
            path = p

        key = (label, str(path))
        if key not in seen:
            out.append((label, path))
            seen.add(key)

    return out

def load_config(path: Path) -> dict:
    """Load pixarr.toml if present; return {} on any issue."""
    if not path.exists():
        return {}
    try:
        return toml.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def build_quarantine_cfg(cfg_quar: dict) -> Dict[str, bool]:
    """Create the effective quarantine policy from TOML (defaults are safe)."""
    return {
        "junk":              bool(cfg_quar.get("junk",              True)),
        "unsupported_ext":   bool(cfg_quar.get("unsupported_ext",   True)),
        "zero_bytes":        bool(cfg_quar.get("zero_bytes",        True)),
        "stat_error":        bool(cfg_quar.get("stat_error",        True)),
        "move_failed":       bool(cfg_quar.get("move_failed",       True)),
        "dupes":             bool(cfg_quar.get("dupes",             True)),
        "missing_datetime":  bool(cfg_quar.get("missing_datetime",  True)),
    }

# ---------- File helpers ----------

def sha256_file(p: Path, bufsize: int = 1024*1024) -> str:
    h = hashlib.sha256()
    with p.open("rb", buffering=0) as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def exiftool_json(p: Path) -> dict:
    """Return metadata dict from exiftool -j (or {})."""
    try:
        out = subprocess.check_output(
            [EXIFTOOL_PATH, "-j", "-n", "-api", "largefilesupport=1", str(p)],
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        arr = json.loads(out.decode("utf-8", errors="ignore"))
        return arr[0] if arr else {}
    except Exception:
        return {}

def is_media_candidate(p: Path) -> bool:
    # only look at extension here; let stat() decide file health
    return p.suffix.lower() in SUPPORTED_EXT

# --- Content hash (decoded pixels, stable across EXIF/XMP edits) ------------------------  # [CONTENT HASH]
def compute_image_content_sha256(path: Path) -> Optional[str]:
    """
    Return a SHA-256 hex digest of the decoded pixels for an image file.
    - Applies EXIF orientation (so rotated vs not-rotated match).
    - Converts everything to RGB deterministically.
    - Flattens alpha on black to avoid ambiguity.
    NOTE: This is NOT perceptual hashing. If pixels change (resize/crop/recompress),
    the digest changes. Returns None if Pillow or decoder is unavailable.
    """
    if Image is None or ImageOps is None:
        return None
    try:
        with Image.open(path) as im:
            im.load()  # force decode to surface errors
            im = ImageOps.exif_transpose(im)
            if "A" in im.getbands():
                # composite on opaque black, then drop alpha
                base = Image.new("RGBA", im.size, (0, 0, 0, 255))
                im = Image.alpha_composite(base, im.convert("RGBA")).convert("RGB")
            else:
                im = im.convert("RGB")
            header = f"{im.mode}|{im.width}x{im.height}".encode("utf-8")
            raw = im.tobytes()
            h = hashlib.sha256()
            h.update(header)
            h.update(raw)
            return h.hexdigest()
    except Exception:
        return None

# Only capture/camera-origin dates. (File dates optionally added via flag)
_DATE_KEYS = [
    "DateTimeOriginal",
    "CreateDate",           # EXIF create
    "MediaCreateDate",      # some video containers
    "TrackCreateDate",      # some MP4/MOV tracks
    "QuickTime:CreateDate", # QuickTime atom
    "QuickTime:CreationDate"
]

_dt_re = re.compile(
    r"^(?P<y>\d{4}):(?P<m>\d{2}):(?P<d>\d{2})[ T]"
    r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})"
    r"(?:\.(?P<sub>\d+))?(?P<tz>Z|[+\-]\d{2}:?\d{2})?$"
)

def _parse_exif_dt(s: str) -> Optional[datetime]:
    if s is None:
        return None
    s = str(s).strip()

    # Common invalid/sentinel values → treat as missing
    if not s or s.startswith(("0000:00:00", "0001:01:01")):
        return None
    # Optional: some files default to Unix epoch—skip if you don’t trust it
    if s.startswith("1970:01:01"):
        return None

    m = _dt_re.match(s)
    if m:
        base = s[:19]  # "YYYY:MM:DD HH:MM:SS"
        try:
            dt = datetime.strptime(base, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            return None

        tz = m.group("tz")
        if tz and tz != "Z":
            # normalize "+hhmm" → "+hh:mm"
            tz = tz if ":" in tz else (tz[:3] + ":" + tz[3:])
            try:
                return datetime.fromisoformat(dt.strftime("%Y-%m-%dT%H:%M:%S") + tz)
            except Exception:
                return None
        elif tz == "Z":
            return datetime.fromisoformat(dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00")
        return dt

    # ISO-like fallback some containers emit
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def extract_taken_at_exif_only(meta: dict) -> Optional[str]:
    """Return taken_at only from EXIF/QuickTime tags. Otherwise None."""
    for k in _DATE_KEYS:
        v = meta.get(k)
        if v:
            dt = _parse_exif_dt(str(v))
            if dt:
                return dt.isoformat()
    return None

def last_meaningful_folder(path: Path) -> Optional[str]:
    """Pick last non-generic folder name for hinting."""
    for parent in path.parents:
        name = parent.name
        if not name:
            continue
        n = name.strip().lower().replace("-", " ").replace("_", " ")
        if n in GENERIC_FOLDERS:
            continue
        if any(len(tok) >= 3 for tok in n.split()):
            return name
    return None

def uuid_from_hash(hash_hex: str) -> str:
    """Deterministic UUID from SHA-256 (stable per content)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, hash_hex))

def file_token_for(p: Path, h: Optional[str] = None) -> str:
    if h:
        return h[:8]
    try:
        return hashlib.sha1(str(p).encode("utf-8", "ignore")).hexdigest()[:8]
    except Exception:
        return "-"


def canonical_name(taken_at_iso: str, hash_hex: str, ext: str) -> str:
    """YYYY-MM-DD_HH-MM-SS_hashprefix.ext (hashprefix = first 8 chars)."""
    dt = datetime.fromisoformat(taken_at_iso.replace("Z", "+00:00"))
    stamp = dt.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}_{hash_hex[:8]}{ext.lower()}"

def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column if it doesn't exist. Safe to call every run."""
    cur = conn.execute(f"PRAGMA table_info({table});")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")
        conn.commit()

def plan_nonclobber(dest_dir: Path, filename: str) -> Path:
    """Choose a destination path that doesn't overwrite existing files."""
    base = dest_dir / filename
    if not base.exists():
        return base
    stem, ext = base.stem, base.suffix
    i = 2
    while True:
        candidate = base.with_name(f"{stem}_{i}{ext}")
        if not candidate.exists():
            return candidate
        i += 1

def _write_quarantine_sidecar(dest: Path, payload: dict) -> None:
    try:
        (dest.parent / (dest.name + ".quarantine.json")).write_text(
            json.dumps(payload, indent=2)
        )
    except Exception:
        pass

def quarantine_file(src: Path, reason: str, ingest_id: str, extra: Optional[str] = None) -> Optional[Path]:
    """Move/copy the src file to Quarantine/<mapped-subdir>/ and write a tiny sidecar JSON."""
    # Map duplicate reasons to the unified 'duplicate' subdir; otherwise use the reason.  # [DUPES]
    subdir = REASON_TO_SUBDIR.get(reason, reason)
    dest_dir = QUARANTINE_ROOT / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = plan_nonclobber(dest_dir, src.name)
    try:
        src.rename(dest)
        moved = True
    except Exception as e1:
        try:
            shutil.copy2(src, dest)
            moved = True
            try:
                src.unlink()
            except Exception:
                pass
        except Exception:
            moved = False

    payload = {
        "reason": reason,
        "ingest_id": ingest_id,
        "original_path": str(src),
        "quarantined_to": str(dest) if moved else None,
        "timestamp": datetime.utcnow().isoformat(),
        "extra": extra,  # include dupe basis / canonical id info when provided        # [DUPES]
    }
    _write_quarantine_sidecar(dest if moved else dest_dir / (src.name + ".failed"), payload)
    return dest if moved else None

def maybe_quarantine(
    src: Path,
    reason: str,
    ingest_id: str,
    *,
    extra: Optional[str] = None,
    source: str,
    file_token: Optional[str] = None
) -> Optional[Path]:
    level = logging.WARNING
    msg = f"QUARANTINE {src} -> {reason} ({extra or ''})"
    _extra = {"ingest_id": ingest_id, "source": source, "file_token": file_token or ingest_id[:8]}
    if DRY_RUN:
        LOGGER.log(level, f"[DRY] {msg}", extra=_extra)
        return None
    q = quarantine_file(src, reason, ingest_id, extra=extra)
    if q:
        LOGGER.log(level, f"{msg} -> {q}", extra=_extra)
        return q
    LOGGER.error(f"QUARANTINE FAILED for {src} ({reason})", extra=_extra)
    return None


def setup_logging(data_dir: Path, logs_dir_arg: Optional[str], verbose: int,
                  quiet: bool, log_level_arg: Optional[str], json_logs: bool) -> logging.Logger:
    """
    Console/File matrix:
      - -q:   console = silent;        file = INFO only (drop WARNING+)
      - none: console = INFO only;     file = INFO+ (INFO & WARNING)
      - -v:   console = INFO+;         file = INFO+ (INFO & WARNING)
      - -vv:  console = DEBUG;         file = DEBUG
      - --log-level=X: both console & file use X (no special filters)
    """

    class EnsureContext(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "source"):   record.source = "-"
            if not hasattr(record, "ingest_id"): record.ingest_id = "-"
             # Default file_token to ingest_id unless the log call overrides it
            if not hasattr(record, "file_token"): record.file_token = record.ingest_id
            return True

    class MaxLevelFilter(logging.Filter):
        """Allow records up to and including `levelno` (drop anything higher)."""
        def __init__(self, levelno: int): super().__init__(); self.levelno = levelno
        def filter(self, record: logging.LogRecord) -> bool: return record.levelno <= self.levelno

    logger = logging.getLogger("pixarr")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers): logger.removeHandler(h)

    # Decide levels & max-filters
    if log_level_arg:
        console_level = getattr(logging, log_level_arg.upper())
        file_level    = console_level
        console_max   = None
        file_max      = None
    else:
        if quiet:
            console_level = logging.CRITICAL   # prints nothing (we don't emit CRITICAL)
            file_level    = logging.INFO       # keep audit trail at INFO
            console_max   = None               # already silent
            file_max      = MaxLevelFilter(logging.INFO)  # drop WARNING+
        elif verbose >= 2:
            console_level = logging.DEBUG
            file_level    = logging.DEBUG
            console_max   = None
            file_max      = None
        elif verbose >= 1:
            console_level = logging.INFO
            file_level    = logging.INFO
            console_max   = None               # show INFO & WARNING on console
            file_max      = None               # INFO & WARNING in file
        else:
            # default: console shows ONLY INFO (hide WARNING); file keeps INFO & WARNING
            console_level = logging.INFO
            file_level    = logging.INFO
            console_max   = MaxLevelFilter(logging.INFO)
            file_max      = None

    # Console handler (human format)
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.addFilter(EnsureContext())
    if console_max: ch.addFilter(console_max)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    # File handler (rotating)
    logs_dir = Path(logs_dir_arg) if logs_dir_arg else (data_dir / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"pixarr-{ts}.log"

    fh = logging.handlers.TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=14, encoding="utf-8"
    )
    fh.setLevel(file_level)
    fh.addFilter(EnsureContext())
    if file_max: fh.addFilter(file_max)

    if json_logs:
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "ts": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
                    "level": record.levelname,
                    "msg": record.getMessage(),
                    "name": record.name,
                    "source": getattr(record, "source", None),
                    "ingest_id": getattr(record, "ingest_id", None),
                    "file_token": getattr(record, "file_token", None),
                }
                if record.exc_info:
                    payload["exc"] = self.formatException(record.exc_info)
                return json.dumps(payload, ensure_ascii=False)
        fh.setFormatter(JsonFormatter())
    else:
        fh.setFormatter(logging.Formatter(
            "%(asctime)sZ [%(levelname)s] [%(source)s:%(file_token)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"
        ))
    logger.addHandler(fh)

    logger.debug(f"Log file: {log_path}")
    return logger


# ---------- DB ops ----------

def begin_ingest(conn: sqlite3.Connection, source: str, note: Optional[str] = None) -> str:
    """Create a media ingest row and return its UUID."""
    ingest_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO ingests (id, source, started_at, notes) VALUES (?, ?, ?, ?)",
        (ingest_id, source, datetime.utcnow().isoformat(), note),
    )
    conn.commit()
    return ingest_id

def finish_ingest(conn: sqlite3.Connection, ingest_id: str) -> None:
    conn.execute(
        "UPDATE ingests SET finished_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), ingest_id),
    )
    conn.commit()

def upsert_media(conn: sqlite3.Connection, row: dict) -> Tuple[str, str, Optional[str]]:
    """
    Insert a media row or update existing by hash.
    Return (id, state, canonical_path).
    NOTE: now includes content_sha256 column.                                   # [CONTENT HASH]
    """
    now = datetime.utcnow().isoformat()
    row.setdefault("added_at", now)
    row["updated_at"] = now

    try:
        conn.execute(
            """
            INSERT INTO media (
              id, hash_sha256, phash, content_sha256, ext, bytes, taken_at, tz_offset,
              gps_lat, gps_lon, state, canonical_path,
              added_at, updated_at, xmp_written, quarantine_reason
            ) VALUES (
              :id, :hash_sha256, :phash, :content_sha256, :ext, :bytes, :taken_at, :tz_offset,
              :gps_lat, :gps_lon, :state, :canonical_path,
              :added_at, :updated_at, :xmp_written, :quarantine_reason
            )
            """,
            row,
        )
    except sqlite3.IntegrityError:
        conn.execute(
            """
            UPDATE media
            SET taken_at       = COALESCE(media.taken_at, :taken_at),
                gps_lat        = COALESCE(media.gps_lat,  :gps_lat),
                gps_lon        = COALESCE(media.gps_lon,  :gps_lon),
                content_sha256 = COALESCE(:content_sha256, media.content_sha256),
                state          = CASE
                                    WHEN media.state IN ('library','quarantine','deleted')
                                         THEN media.state
                                    ELSE :state
                                 END,
                canonical_path = COALESCE(:canonical_path, media.canonical_path),
                quarantine_reason = CASE
                                      WHEN :state='quarantine' THEN :quarantine_reason
                                      ELSE NULL
                                    END,
                updated_at     = :updated_at
            WHERE hash_sha256 = :hash_sha256
            """,
            {**row, "updated_at": now},
        )

    cur = conn.execute(
        "SELECT id, state, canonical_path FROM media WHERE hash_sha256=?",
        (row["hash_sha256"],),
    )
    mid, st, cpath = cur.fetchone()
    return mid, st, cpath


def insert_sighting(conn: sqlite3.Connection, media_id: str, full_path: Path,
                    filename: str, source_root: str, folder_hint: Optional[str],
                    ingest_id: str) -> None:
    conn.execute(
        """
        INSERT INTO sightings
          (media_id, source_root, full_path, filename, folder_hint, seen_at, ingest_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (media_id, source_root, str(full_path), filename, folder_hint,
         datetime.utcnow().isoformat(), ingest_id),
    )

def already_finalized(conn: sqlite3.Connection, hash_hex: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM media WHERE hash_sha256=? AND state='library' LIMIT 1",
        (hash_hex,),
    )
    return cur.fetchone() is not None

# [DUPES] helper: prefer library over review when matching by file or content hash
def _find_canonical_by_filehash(conn: sqlite3.Connection, h: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Return (id, state, canonical_path) for the best match by exact file hash,
    preferring 'library' over 'review'. None if not found.
    """
    row = conn.execute(
        """
        SELECT id, state, canonical_path
        FROM media
        WHERE hash_sha256 = ?
          AND state IN ('library','review')
        ORDER BY CASE state WHEN 'library' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (h,),
    ).fetchone()
    return tuple(row) if row else None

def _find_canonical_by_contenthash(conn: sqlite3.Connection, c: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Return (id, state, canonical_path) for the best match by content hash,
    preferring 'library' over 'review'. None if not found.
    """
    if not c:
        return None
    row = conn.execute(
        """
        SELECT id, state, canonical_path
        FROM media
        WHERE content_sha256 = ?
          AND state IN ('library','review')
        ORDER BY CASE state WHEN 'library' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (c,),
    ).fetchone()
    return tuple(row) if row else None

# ---------- Date from filename + resolver ----------

def _taken_from_filename(name: str) -> Optional[datetime]:
    """
    Parse common filename timestamp patterns and return a datetime, or None.
    Examples handled:
      - PHOTO-2024-07-10-20-08-42.jpg
      - IMG_20240710_200842.HEIC
      - 2024-07-10 20.08.42.jpg
      - WhatsApp Image 2024-07-10 at 20.08.42.jpeg
      - PXL_20240710_200842123.jpg  (uses first HHMMSS after date)
    """
    s = name

    # PHOTO-YYYY-MM-DD-HH-MM-SS
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[-_ T]+(\d{2})[-_.:](\d{2})[-_.:](\d{2})", s)
    if m:
        y, mo, d, H, M, S = map(int, m.groups())
        return datetime(y, mo, d, H, M, S)

    # YYYYMMDD[_-]HHMMSS  (e.g., IMG_20240710_200842)
    m = re.search(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", s)
    if m:
        y, mo, d, H, M, S = map(int, m.groups())
        return datetime(y, mo, d, H, M, S)

    # WhatsApp-style: YYYY-MM-DD at HH.MM.SS
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s+at\s+(\d{2})[.:](\d{2})[.:](\d{2})", s, re.IGNORECASE)
    if m:
        y, mo, d, H, M, S = map(int, m.groups())
        return datetime(y, mo, d, H, M, S)

    # Loose: YYYY-MM-DD[ _]HH.MM.SS
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[.:](\d{2})[.:](\d{2})", s)
    if m:
        y, mo, d, H, M, S = map(int, m.groups())
        return datetime(y, mo, d, H, M, S)

    # Google PXL_: PXL_YYYYMMDD_HHMMSS...
    m = re.search(r"PXL_(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", s, re.IGNORECASE)
    if m:
        y, mo, d, H, M, S = map(int, m.groups())
        return datetime(y, mo, d, H, M, S)

    return None

def resolve_taken_at(meta: dict, filename: str, allow_filename_dates: bool) -> Optional[str]:
    """
    Decide the capture time:
      1) EXIF/QuickTime tags (strict)
      2) filename-derived (only if allow_filename_dates=True)
      -> return ISO8601 string or None
    """
    # 1) strict EXIF/QuickTime
    t = extract_taken_at_exif_only(meta)
    if t:
        return t
    # 2) optional filename fallback
    if allow_filename_dates:
        dt = _taken_from_filename(filename)
        if dt:
            return dt.isoformat()
    return None

# ---------- Ingest core ----------
def ingest_one_source(conn, source_label, staging_root, *, on_review_dupe: str, note=None, heartbeat=500):
    """Run a full ingest pass for one staging root and return stats for end-of-run summary."""
    stats = {
        "label": source_label,
        "path": str(staging_root),
        "ingest_id": None,
        "scanned": 0,
        "moved": 0,           # planned in dry-run, actual in write mode
        "updated": 0,
        "skipped_dupe": 0,
        "quarantined": 0,
        "q_counts": defaultdict(int),  # reason -> count
    }

    if not staging_root.exists():
        log(f"SKIP {source_label}: path not found -> {staging_root}")
        return stats

    ingest_id = begin_ingest(conn, source_label, note)
    stats["ingest_id"] = ingest_id
    ctx = batch_logger(ingest_id, source_label)

    log(f"\n=== {source_label} ===")
    ctx.info("Started ingest batch: %s (%s)", ingest_id, staging_root)

    try:
        for root, dirs, files in os.walk(staging_root):
            # prune system dirs and AppleDouble dir entries
            dirs[:] = [d for d in dirs if d not in DIR_IGNORE and not d.startswith("._")]

            for name in files:
                p = Path(root) / name
                try:
                    # junk files and AppleDouble resource forks
                    if name in JUNK_FILES or any(name.startswith(pref) for pref in JUNK_PREFIXES):
                        if QUAR.get("junk", True):
                            stats["q_counts"]["junk"] += 1
                            maybe_quarantine(p, "junk", ingest_id, extra=("appledouble" if name.startswith("._") else "system_file"), source=source_label, file_token=file_token_for(p))
                            stats["quarantined"] += 1
                        continue

                    # unsupported extensions (driven by config-overridden SUPPORTED_EXT)
                    if not is_media_candidate(p):
                        if QUAR.get("unsupported_ext", True):
                            stats["q_counts"]["unsupported_ext"] += 1
                            maybe_quarantine(p, "unsupported_ext", ingest_id, extra=p.suffix.lower(), source=source_label, file_token=file_token_for(p))
                            stats["quarantined"] += 1
                        continue

                    stats["scanned"] += 1

                    # heartbeat (env overrides arg)
                    hb = int(os.environ.get("PIXARR_HEARTBEAT", heartbeat))
                    if hb > 0 and stats["scanned"] % hb == 0:
                        ctx.info("… scanned=%d moved=%d quarantined=%d dupes=%d",
                                 stats["scanned"], stats["moved"], stats["quarantined"], stats["skipped_dupe"])

                    # per-file logic
                    try:
                        size = p.stat().st_size
                    except Exception as e:
                        if QUAR.get("stat_error", True):
                            stats["q_counts"]["stat_error"] += 1
                            maybe_quarantine(p, "stat_error", ingest_id, extra=str(e), source=source_label, file_token=file_token_for(p))
                            stats["quarantined"] += 1
                        continue

                    if size == 0:
                        if QUAR.get("zero_bytes", True):
                            stats["q_counts"]["zero_bytes"] += 1
                            maybe_quarantine(p, "zero_bytes", ingest_id, source=source_label, file_token=file_token_for(p))
                            stats["quarantined"] += 1
                        continue

                    h = sha256_file(p)
                    tok = file_token_for(p, h)
                    meta = exiftool_json(p)
                    ext = p.suffix.lower()
                    hint = last_meaningful_folder(p.parent)

                    # -------- [CONTENT HASH] compute pixel-level digest for images (if possible) -----
                    content_sha256: Optional[str] = None
                    if ext in IMAGE_EXT:  # RAWs are NOT in IMAGE_EXT (we subtract RAW from images)
                        content_sha256 = compute_image_content_sha256(p)
                    # ---------------------------------------------------------------------------------

                    # -------- [DUPES] Early duplicate resolution: exact file, then content ----------
                    # 1) By exact file hash (prefer library over review)
                    canonical = _find_canonical_by_filehash(conn, h)
                    if canonical:
                        canon_id, canon_state, _canon_path = canonical
                        reason = "duplicate_in_library" if canon_state == "library" else "duplicate_in_review"
                        basis = "file"
                        # Policy: for duplicates in review, honor on_review_dupe; library always quarantined if enabled
                        if reason == "duplicate_in_review" and on_review_dupe == "ignore":
                            stats["updated"] += 1
                            stats["skipped_dupe"] += 1
                            now = datetime.utcnow().isoformat()
                            conn.execute("UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?", (now, now, canon_id))
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            conn.commit()
                            ctx.debug("= Already tracked (review, basis=file): %s", p, extra={"file_token": tok})
                            continue
                        elif reason == "duplicate_in_review" and on_review_dupe == "delete":
                            stats["skipped_dupe"] += 1
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            if DRY_RUN:
                                ctx.info("[DRY] DELETE duplicate (review, basis=file): %s", p, extra={"file_token": tok})
                            else:
                                try:
                                    p.unlink()
                                except Exception:
                                    stats["q_counts"]["move_failed"] += 1
                                    maybe_quarantine(p, "move_failed", ingest_id, extra="delete_failed", source=source_label, file_token=tok)
                                    stats["quarantined"] += 1
                            conn.commit()
                            continue
                        else:
                            if QUAR.get("dupes", True):
                                stats["q_counts"][reason] += 1
                                extra = f"basis={basis} dupe_of={canon_id}"
                                maybe_quarantine(p, reason, ingest_id, extra=extra, source=source_label, file_token=tok)
                                stats["quarantined"] += 1
                            stats["skipped_dupe"] += 1
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            conn.commit()
                            continue

                    # 2) By content hash (prefer library over review)
                    canonical = _find_canonical_by_contenthash(conn, content_sha256) if content_sha256 else None
                    if canonical:
                        canon_id, canon_state, _canon_path = canonical
                        reason = "duplicate_in_library" if canon_state == "library" else "duplicate_in_review"
                        basis = "content"
                        if reason == "duplicate_in_review" and on_review_dupe == "ignore":
                            stats["updated"] += 1
                            stats["skipped_dupe"] += 1
                            now = datetime.utcnow().isoformat()
                            conn.execute("UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?", (now, now, canon_id))
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            conn.commit()
                            ctx.debug("= Already tracked (review, basis=content): %s", p, extra={"file_token": tok})
                            continue
                        elif reason == "duplicate_in_review" and on_review_dupe == "delete":
                            stats["skipped_dupe"] += 1
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            if DRY_RUN:
                                ctx.info("[DRY] DELETE duplicate (review, basis=content): %s", p, extra={"file_token": tok})
                            else:
                                try:
                                    p.unlink()
                                except Exception:
                                    stats["q_counts"]["move_failed"] += 1
                                    maybe_quarantine(p, "move_failed", ingest_id, extra="delete_failed", source=source_label, file_token=tok)
                                    stats["quarantined"] += 1
                            conn.commit()
                            continue
                        else:
                            if QUAR.get("dupes", True):
                                stats["q_counts"][reason] += 1
                                extra = f"basis={basis} dupe_of={canon_id}"
                                maybe_quarantine(p, reason, ingest_id, extra=extra, source=source_label, file_token=tok)
                                stats["quarantined"] += 1
                            stats["skipped_dupe"] += 1
                            insert_sighting(conn, canon_id, p, name, source_label, hint, ingest_id)
                            conn.commit()
                            continue
                    # ---------------------------------------------------------------------------------

                    # -------- capture time resolution (only for non-duplicates) --------
                    taken_at = resolve_taken_at(meta, name, ALLOW_FILENAME_DATES)
                    if not taken_at:
                        reason_code = "missing_datetime"
                        reason_msg  = "no capture date (exif/qt" + ("/filename" if ALLOW_FILENAME_DATES else "") + ")"

                        q_dest = None
                        if QUAR.get("missing_datetime", True):
                            q_dest = maybe_quarantine(p, reason_code, ingest_id, extra=reason_msg,  source=source_label, file_token=tok)

                        # track in DB as quarantined (only for *media-like* files where we have a hash)
                        media_row = {
                            "id": uuid_from_hash(h),
                            "hash_sha256": h,
                            "phash": None,
                            "content_sha256": content_sha256,  # [CONTENT HASH]
                            "ext": ext,
                            "bytes": size,
                            "taken_at": None,
                            "tz_offset": None,
                            "gps_lat": meta.get("GPSLatitude") if meta.get("GPSLatitude") is not None else None,
                            "gps_lon": meta.get("GPSLongitude") if meta.get("GPSLongitude") is not None else None,
                            "state": "quarantine",
                            "canonical_path": str(q_dest) if q_dest and not DRY_RUN else None,
                            "added_at": datetime.utcnow().isoformat(),
                            "updated_at": datetime.utcnow().isoformat(),
                            "xmp_written": 0,
                            "quarantine_reason": reason_code,
                        }
                        mid, _, _ = upsert_media(conn, media_row)
                        insert_sighting(conn, mid, p, name, source_label, hint, ingest_id)
                        stats["q_counts"][reason_code] += 1
                        stats["quarantined"] += 1
                        conn.commit()
                        continue
                    # -----------------------------------------

                    media_row = {
                        "id": uuid_from_hash(h),
                        "hash_sha256": h,
                        "phash": None,
                        "content_sha256": content_sha256,  # [CONTENT HASH]
                        "ext": ext,
                        "bytes": size,
                        "taken_at": taken_at,
                        "tz_offset": None,
                        "gps_lat": meta.get("GPSLatitude") if meta.get("GPSLatitude") is not None else None,
                        "gps_lon": meta.get("GPSLongitude") if meta.get("GPSLongitude") is not None else None,
                        "state": "review",
                        "canonical_path": None,
                        "added_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat(),
                        "xmp_written": 0,
                        "quarantine_reason": None,  # ensure UPDATE clears any previous reason
                    }

                    mid, current_state, current_canon = upsert_media(conn, media_row)
                    insert_sighting(conn, mid, p, name, source_label, hint, ingest_id)

                    # (legacy safety) exact-file dupes detected *after* upsert (should be rare now)
                    if already_finalized(conn, h):
                        stats["skipped_dupe"] += 1
                        ctx.debug("= DUP in library (late): %s (%s)", p, h[:8], extra={"file_token": tok})
                        if QUAR.get("dupes", True):
                            stats["q_counts"]["duplicate_in_library"] += 1
                            maybe_quarantine(p, "duplicate_in_library", ingest_id, extra="basis=file (late)", source=source_label, file_token=tok)
                            stats["quarantined"] += 1
                        conn.commit()
                        continue

                    if current_state in ("review", "library") and current_canon:
                        try:
                            canon_missing = not Path(current_canon).exists()
                        except Exception:
                            canon_missing = True

                        if not canon_missing:
                            if current_state == "library":
                                stats["skipped_dupe"] += 1
                                if QUAR.get("dupes", True):
                                    stats["q_counts"]["duplicate_in_library"] += 1
                                    maybe_quarantine(p, "duplicate_in_library", ingest_id, extra="basis=file (late-state)", source=source_label, file_token=tok)
                                    stats["quarantined"] += 1
                                conn.commit()
                                continue

                            # current_state == "review"
                            if on_review_dupe == "ignore":
                                stats["updated"] += 1
                                now = datetime.utcnow().isoformat()
                                conn.execute(
                                    "UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?",
                                    (now, now, mid),
                                )
                                ctx.debug("= Already tracked (review, late): %s", current_canon, extra={"file_token": tok})
                                conn.commit()
                                continue
                            elif on_review_dupe == "quarantine":
                                if QUAR.get("dupes", True):
                                    stats["q_counts"]["duplicate_in_review"] += 1
                                    maybe_quarantine(p, "duplicate_in_review", ingest_id, extra="basis=file (late-state)", source=source_label, file_token=tok)
                                    stats["quarantined"] += 1
                                else:
                                    stats["updated"] += 1
                                    now = datetime.utcnow().isoformat()
                                    conn.execute(
                                        "UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?",
                                        (now, now, mid),
                                    )
                                conn.commit()
                                continue
                            elif on_review_dupe == "delete":
                                if DRY_RUN:
                                    ctx.info("[DRY] DELETE duplicate (review, late): %s", p, extra={"file_token": tok})
                                else:
                                    try:
                                        p.unlink()
                                    except Exception:
                                        stats["q_counts"]["move_failed"] += 1
                                        maybe_quarantine(p, "move_failed", ingest_id, extra="delete_failed", source=source_label, file_token=tok)
                                        stats["quarantined"] += 1
                                stats["skipped_dupe"] += 1
                                conn.commit()
                                continue

                    fname = canonical_name(taken_at, h, ext)
                    dest = plan_nonclobber(REVIEW_ROOT, fname)

                    if DRY_RUN:
                        ctx.debug("[DRY] MOVE %s -> %s", p, dest, extra={"file_token": tok})
                        stats["moved"] += 1
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        moved_ok = False
                        try:
                            p.rename(dest)
                            moved_ok = True
                        except Exception as e1:
                            try:
                                shutil.copy2(p, dest)
                                moved_ok = True
                                try:
                                    p.unlink()
                                except Exception:
                                    pass
                            except Exception as e2:
                                moved_ok = False
                                if QUAR.get("move_failed", True):
                                    q_path = maybe_quarantine(p, "move_failed", ingest_id, extra=f"{e1} | {e2}", source=source_label, file_token=tok)
                                    # Flip the row to quarantined since the move didn't succeed
                                    now = datetime.utcnow().isoformat()
                                    conn.execute(
                                        """
                                        UPDATE media
                                        SET state='quarantine',
                                            canonical_path=?,
                                            quarantine_reason='move_failed',
                                            updated_at=?
                                        WHERE id=?
                                        """,
                                        (str(q_path) if q_path and not DRY_RUN else None, now, mid),
                                    )
                                    stats["q_counts"]["move_failed"] += 1
                                    stats["quarantined"] += 1

                        if moved_ok:
                            now = datetime.utcnow().isoformat()
                            conn.execute(
                                "UPDATE media SET state='review', canonical_path=?, updated_at=?, last_verified_at=? WHERE id=?",
                                (str(dest), now, now, mid),
                            )
                            ctx.debug("MOVED %s -> %s", p, dest, extra={"file_token": tok})
                            stats["moved"] += 1

                    conn.commit()

                except Exception:
                    # Catch-all so one bad file doesn't kill the batch
                    ctx.exception("Unhandled error while processing %s", p, extra={"file_token": file_token_for(p)})
                    continue

    finally:
        finish_ingest(conn, ingest_id)

    # Solidify defaultdict for JSON-like printing
    stats["q_counts"] = dict(stats["q_counts"])
    return stats

# ---------- Main ----------

def main():
    # Load config first (single read)
    cfg_path = repo_root() / "pixarr.toml"
    cfg = load_config(cfg_path)

    # Read defaults from config (fall back to current behavior)
    cfg_paths = cfg.get("paths", {})
    cfg_ingest = cfg.get("ingest", {})
    cfg_quar = cfg.get("quarantine", {})
    default_on_review_dupe = cfg_ingest.get("on_review_dupe", "quarantine")

    # -------------------------------------------------------------------------
    # OVERRIDE extension sets from config so ingest + API/UI are consistent
    # -------------------------------------------------------------------------
    global IMAGE_EXT, RAW_EXT, VIDEO_EXT, SUPPORTED_EXT
    _images_cfg, _raw_cfg, _videos_cfg = _formats_from_cfg_dict(cfg)
    # IMAGE_EXT should exclude RAW (non-RAW images only) for pixel hashing:
    IMAGE_EXT = _images_cfg - _raw_cfg
    RAW_EXT   = _raw_cfg
    VIDEO_EXT = _videos_cfg
    SUPPORTED_EXT = IMAGE_EXT | RAW_EXT | VIDEO_EXT   # everything we accept

    parser = argparse.ArgumentParser(description="Pixarr: ingest media from staging folders.")
    parser.add_argument("sources", nargs="*", help="Subset of sources to ingest (pc, other, icloud, sdcard)")
    parser.add_argument("-n", "--note", help="Optional note to attach to this ingest batch")
    parser.add_argument("--write", action="store_true",
                        default=not cfg_ingest.get("dry_run_default", True),
                        help="Perform moves/copies (default is dry-run)")
    parser.add_argument("--data-dir",
                        default=str(Path(cfg_paths.get("data_dir", str(repo_root() / "data")))),
                        help="Root data directory (default: ./data under repo)")
    parser.add_argument("--allow-file-dates", action="store_true",
                        default=bool(cfg_ingest.get("allow_file_dates", False)),
                        help="Allow ModifyDate/FileModifyDate as capture time fallback")
    parser.add_argument("--allow-filename-dates", action="store_true",
                        default=bool(cfg_ingest.get("allow_filename_dates", False)),
                        help="Allow filename-derived timestamps as fallback (e.g., PHOTO-YYYY-MM-DD-HH-MM-SS.jpg)")
    parser.add_argument("--logs-dir", default=None,
                        help="Where to write log files (default: <data_dir>/logs)")
    parser.add_argument("--log-level", default=None, choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL"],
                        help="Force console log level (overrides -v/-q)")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="Increase console verbosity (repeatable)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Minimal console output")
    parser.add_argument("--json-logs", action="store_true",
                        help="Write JSON-formatted logs to file handler")
    parser.add_argument("--heartbeat", type=int, default=500,
                        help="Emit a progress line every N scanned files (default 500)")

    parser.add_argument("--on-review-dupe", choices=["ignore", "quarantine", "delete"], 
                        help=f"Policy when a duplicate already exists in Review (default from config: {default_on_review_dupe})")

    # ---- after cfg_* are read, before parser.parse_args()
    valid_dupe_policies = {"ignore", "quarantine", "delete"}     # the only allowed values
    default_on_review_dupe = cfg_ingest.get("on_review_dupe", "quarantine")  # read from pixarr.toml (or use 'quarantine')
    if default_on_review_dupe not in valid_dupe_policies:        # if the config had a typo/bad value…
        default_on_review_dupe = "quarantine"                    # …fall back safely (can’t log yet)

    args = parser.parse_args()
    on_review_dupe = args.on_review_dupe or default_on_review_dupe

    # setup logging
    global LOGGER
    LOGGER = setup_logging(
        data_dir=Path(args.data_dir).resolve(),
        logs_dir_arg=args.logs_dir,
        verbose=args.verbose,
        quiet=args.quiet,
        log_level_arg=args.log_level,
        json_logs=args.json_logs,
    )

    # Build date keys dynamically from the flags
    base_keys = [
        "DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate",
        "QuickTime:CreateDate", "QuickTime:CreationDate"
    ]
    if args.allow_file_dates:
        base_keys += ["ModifyDate", "FileModifyDate"]

    global _DATE_KEYS
    _DATE_KEYS = base_keys

    # Expose filename-dates flag to the ingest loop
    global ALLOW_FILENAME_DATES
    ALLOW_FILENAME_DATES = args.allow_filename_dates

    # Apply quarantine settings from config
    global QUAR
    QUAR = build_quarantine_cfg(cfg_quar)

    # Paths / bootstrap
    base = Path(args.data_dir).resolve()
    pathize(base)

    global DRY_RUN
    DRY_RUN = not args.write

    ensure_dirs()
    if not DB_PATH.exists():
        ensure_db()

    # exiftool check
    ensure_exiftool()

    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)

    mode = "DRY-RUN" if DRY_RUN else "WRITE"
    log(f"Mode: {mode}")
    log(f"DATA_DIR = {DATA_DIR}")
    log(f"Effective quarantine: {QUAR}")
    log(f"allow_filename_dates={ALLOW_FILENAME_DATES}, allow_file_dates={'ModifyDate' in _DATE_KEYS}")
    log(f"Formats -> images(non-RAW)={sorted(IMAGE_EXT)}, raw={sorted(RAW_EXT)}, videos={sorted(VIDEO_EXT)}")

    t0 = time.perf_counter()

    conn = open_db()
    # upgrade columns if the DB pre-dates these fields
    ensure_column(conn, "sightings", "folder_hint", "TEXT")
    ensure_column(conn, "sightings", "ingest_id", "TEXT")
    ensure_column(conn, "media", "quarantine_reason", "TEXT")
    ensure_column(conn, "media", "content_sha256", "TEXT")  # ensure the new column exists          # [CONTENT HASH]

    # pick sources/paths (works with 'pc', 'other/trip1', absolute paths, etc.)
    try:
        selected = resolve_source_tokens(args.sources)
    except NameError:
        wanted = set(args.sources)
        selected = []
        for label, path in STAGING_SOURCES.items():
            short = label.split("/", 1)[-1]
            if wanted and short not in wanted and label not in wanted:
                continue
            selected.append((label, path))

    all_stats = []
    for label, path in selected:
        stats = ingest_one_source(
            conn, label, path,
            on_review_dupe=on_review_dupe,
            note=args.note,
            heartbeat=args.heartbeat
        )
        all_stats.append(stats)

    conn.close()

    elapsed = time.perf_counter() - t0

    # ---------- END-OF-RUN SUMMARY ----------
    log("\n=== Run summary (grouped) ===")
    for s in all_stats:
        moved_field = f"{s['moved']}(dry)" if DRY_RUN else str(s["moved"])
        log(
            f"Summary {s['label']}: "
            f"scanned={s['scanned']}, moved={moved_field}, updated={s['updated']}, "
            f"skipped_dupe={s['skipped_dupe']}, quarantined={s['quarantined']}"
        )

    # totals
    totals = {
        "scanned": sum(s["scanned"] for s in all_stats),
        "moved": sum(s["moved"] for s in all_stats),
        "updated": sum(s["updated"] for s in all_stats),
        "skipped_dupe": sum(s["skipped_dupe"] for s in all_stats),
        "quarantined": sum(s["quarantined"] for s in all_stats),
    }
    moved_total_field = f"{totals['moved']}(dry)" if DRY_RUN else str(totals["moved"])
    log(
        f"TOTALS: scanned={totals['scanned']}, moved={moved_total_field}, "
        f"updated={totals['updated']}, skipped_dupe={totals['skipped_dupe']}, "
        f"quarantined={totals['quarantined']}"
    )

    # aggregate quarantine reasons
    q_agg = Counter()
    for s in all_stats:
        q_agg.update(s["q_counts"])
    if q_agg:
        log("Quarantine reasons this run:")
        for reason, cnt in q_agg.most_common():
            log(f"  - {reason}: {cnt}")

    # batch info from DB with a few examples per batch
    conn2 = open_db()
    try:
        log("\nBatches created:")
        for s in all_stats:
            if not s["ingest_id"]:
                continue
            iid = s["ingest_id"]
            row = conn2.execute(
                "SELECT source, started_at, finished_at, notes FROM ingests WHERE id=?",
                (iid,)
            ).fetchone()
            if not row:
                continue
            src, started, finished, notes = row
            items = conn2.execute(
                "SELECT COUNT(*) FROM sightings WHERE ingest_id=?",
                (iid,)
            ).fetchone()[0]
            log(f"  {iid[:8]}…  {src}  items={items}  started={started or '-'}  finished={finished or '-'}  note={notes or ''}")

            # last few examples seen in this batch
            examples = conn2.execute(
                """
                SELECT s.filename, m.taken_at, m.canonical_path
                FROM sightings s
                JOIN media m ON s.media_id = m.id
                WHERE s.ingest_id=?
                ORDER BY s.seen_at DESC
                LIMIT 3
                """,
                (iid,)
            ).fetchall()
            for fn, t, cpath in examples:
                log(f"    - {fn} | taken_at={t} | path={cpath}")
    finally:
        conn2.close()

    log(f"\n=== Ingest complete. Total time: {elapsed:.1f} seconds ===")

if __name__ == "__main__":
    main()
