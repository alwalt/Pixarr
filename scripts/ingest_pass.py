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

# ---------- Global toggles ----------
DRY_RUN = True  # overridden by --write

SUPPORTED_EXT = {
    ".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff", ".gif",
    ".mp4", ".mov", ".m4v", ".avi", ".webp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2", ".orf", ".srw"
}
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

def is_supported_media(p: Path) -> bool:
    """Return True if file has one of the supported media extensions."""
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXT

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
    """Move/copy the src file to Quarantine/<reason>/ and write a tiny sidecar JSON."""
    dest_dir = QUARANTINE_ROOT / reason
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
        "extra": extra,
    }
    _write_quarantine_sidecar(dest if moved else dest_dir / (src.name + ".failed"), payload)
    return dest if moved else None

def maybe_quarantine(src: Path, reason: str, ingest_id: str, extra: Optional[str] = None) -> Optional[Path]:
    if DRY_RUN:
        log(f"[DRY] QUARANTINE {src} -> {reason} ({extra or ''})")
        return None
    q = quarantine_file(src, reason, ingest_id, extra=extra)
    if q:
        log(f"QUARANTINED {src} -> {q} ({reason})")
    else:
        log(f"QUARANTINE FAILED for {src} ({reason})")
    return q


def setup_logging(data_dir: Path, logs_dir_arg: Optional[str], verbose: int,
                  quiet: bool, log_level_arg: Optional[str], json_logs: bool) -> logging.Logger:
    """
    Configure console + file logging.
    - Console level: derived from -v/-q or --log-level.
    - File level: INFO (captures summary + actions); DEBUG if verbose>=2.
    - File path: <data_dir>/logs/pixarr-YYYYmmdd_HHMMSS.log (or --logs-dir).
    """

    class EnsureContext(logging.Filter):
        """Guarantee record has .source and .ingest_id so formatters don't explode."""
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "source"):
                record.source = "-"
            if not hasattr(record, "ingest_id"):
                record.ingest_id = "-"
            return True

    logger = logging.getLogger("pixarr")
    logger.setLevel(logging.DEBUG)  # let handlers filter

    # prevent duplicate handlers if re-run in same interpreter
    if logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)

    # Console level
    if log_level_arg:
        console_level = getattr(logging, log_level_arg.upper())
    else:
        if quiet:
            console_level = logging.WARNING
        elif verbose >= 2:
            console_level = logging.DEBUG
        else:
            console_level = logging.INFO

    # Console handler (human format)
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.addFilter(EnsureContext())
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    # File handler
    logs_dir = Path(logs_dir_arg) if logs_dir_arg else (data_dir / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"pixarr-{ts}.log"

    fh = logging.handlers.TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=14, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG if verbose >= 2 else logging.INFO)
    fh.addFilter(EnsureContext())

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
                }
                if record.exc_info:
                    payload["exc"] = self.formatException(record.exc_info)
                return json.dumps(payload, ensure_ascii=False)
        fh.setFormatter(JsonFormatter())
    else:
        fh.setFormatter(logging.Formatter(
            "%(asctime)sZ [%(levelname)s] [%(source)s:%(ingest_id)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"
        ))
    logger.addHandler(fh)

    logger.debug(f"Log file: {log_path}")
    return logger

# ---------- DB ops ----------

def begin_ingest(conn: sqlite3.Connection, source: str, note: Optional[str] = None) -> str:
    """Create an ingests row and return its UUID."""
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
    """
    now = datetime.utcnow().isoformat()
    row.setdefault("added_at", now)
    row["updated_at"] = now

    try:
        conn.execute(
            """
            INSERT INTO media (
              id, hash_sha256, phash, ext, bytes, taken_at, tz_offset,
              gps_lat, gps_lon, state, canonical_path,
              added_at, updated_at, xmp_written, quarantine_reason
            ) VALUES (
              :id, :hash_sha256, :phash, :ext, :bytes, :taken_at, :tz_offset,
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

    # PHOTO-YYYY-MM-DD-HH-MM-SS (allow separators -, _, space and :, . between time parts)
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

def ingest_one_source(conn, source_label, staging_root, note=None, heartbeat=500):
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
                            maybe_quarantine(p, "junk", ingest_id, extra=("appledouble" if name.startswith("._") else "system_file"))
                            stats["quarantined"] += 1
                        continue

                    # unsupported extensions
                    if not is_supported_media(p):
                        if QUAR.get("unsupported_ext", True):
                            stats["q_counts"]["unsupported_ext"] += 1
                            maybe_quarantine(p, "unsupported_ext", ingest_id, extra=p.suffix.lower())
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
                            maybe_quarantine(p, "stat_error", ingest_id, extra=str(e))
                            stats["quarantined"] += 1
                        continue

                    if size == 0:
                        if QUAR.get("zero_bytes", True):
                            stats["q_counts"]["zero_bytes"] += 1
                            maybe_quarantine(p, "zero_bytes", ingest_id)
                            stats["quarantined"] += 1
                        continue

                    h = sha256_file(p)
                    meta = exiftool_json(p)
                    ext = p.suffix.lower()
                    hint = last_meaningful_folder(p.parent)

                    # -------- capture time resolution --------
                    taken_at = resolve_taken_at(meta, name, ALLOW_FILENAME_DATES)
                    if not taken_at:
                        reason_code = "missing_datetime"
                        reason_msg  = "no capture date (exif/qt" + ("/filename" if ALLOW_FILENAME_DATES else "") + ")"

                        q_dest = None
                        if QUAR.get("missing_datetime", True):
                            q_dest = maybe_quarantine(p, reason_code, ingest_id, extra=reason_msg)

                        # track in DB as quarantined (only for *media-like* files where we have a hash)
                        media_row = {
                            "id": uuid_from_hash(h),
                            "hash_sha256": h,
                            "phash": None,
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

                    # already in library
                    if already_finalized(conn, h):
                        stats["skipped_dupe"] += 1
                        ctx.debug("= DUP in library: %s (%s)", p, h[:8])
                        if QUAR.get("dupes", True):
                            stats["q_counts"]["duplicate_in_library"] += 1
                            maybe_quarantine(p, "duplicate_in_library", ingest_id)
                            stats["quarantined"] += 1
                        continue

                    if current_state in ("review", "library") and current_canon:
                        try:
                            canon_missing = not Path(current_canon).exists()
                        except Exception:
                            canon_missing = True

                        if not canon_missing:
                            stats["updated"] += 1
                            now = datetime.utcnow().isoformat()
                            conn.execute(
                                "UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?",
                                (now, now, mid),
                            )
                            ctx.debug("= Already tracked: state=%s path=%s", current_state, current_canon)
                            continue

                        if current_state == "review":
                            ctx.debug("! Missing on disk (review); will requeue -> %s", current_canon)
                        else:
                            ctx.debug("! Missing on disk (library); leaving for reconcile -> %s", current_canon)
                            stats["skipped_dupe"] += 1
                            continue

                    fname = canonical_name(taken_at, h, ext)
                    dest = plan_nonclobber(REVIEW_ROOT, fname)

                    if DRY_RUN:
                        ctx.debug("[DRY] MOVE %s -> %s", p, dest)
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
                                    q_path = maybe_quarantine(p, "move_failed", ingest_id, extra=f"{e1} | {e2}")
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
                            ctx.debug("MOVED %s -> %s", p, dest)
                            stats["moved"] += 1

                    conn.commit()

                except Exception as ex:
                    # Catch-all so one bad file doesn't kill the batch
                    ctx.exception("Unhandled error while processing %s", p)
                    continue

    finally:
        finish_ingest(conn, ingest_id)

    # Solidify defaultdict for JSON-like printing
    stats["q_counts"] = dict(stats["q_counts"])
    return stats

# ---------- Main ----------

def main():
    # Load config first
    cfg = load_config(repo_root() / "pixarr.toml")

    # Read defaults from config (fall back to current behavior)
    cfg_paths = cfg.get("paths", {})
    cfg_ingest = cfg.get("ingest", {})
    cfg_quar = cfg.get("quarantine", {})

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

    args = parser.parse_args()

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

    t0 = time.perf_counter()

    conn = open_db()
    # upgrade columns if the DB pre-dates these fields
    ensure_column(conn, "sightings", "folder_hint", "TEXT")
    ensure_column(conn, "sightings", "ingest_id", "TEXT")
    ensure_column(conn, "media", "quarantine_reason", "TEXT")

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
        stats = ingest_one_source(conn, label, path, note=args.note, heartbeat=args.heartbeat)
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
