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
- Python 3.10+
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
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict

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

def log(msg: str) -> None:
    print(msg, flush=sys.stdout.isatty())

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

def load_config(path: Path) -> dict:
    """Load pixarr.toml if present; return dict of settings."""
    cfg = {}
    try:
        import tomllib  # Python 3.11+
        if path.exists():
            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        try:
            import tomli  # Python 3.10 backport
            if path.exists():
                cfg = tomli.loads(path.read_text(encoding="utf-8"))
        except ModuleNotFoundError:
            pass
    return cfg

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
    s = s.strip()
    m = _dt_re.match(s)
    if m:
        dt = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
        tz = m.group("tz")
        if tz and tz != "Z":
            tz = tz if ":" in tz else (tz[:3] + ":" + tz[3:])
            try:
                return datetime.fromisoformat(dt.strftime("%Y-%m-%dT%H:%M:%S") + tz)
            except Exception:
                pass
        elif tz == "Z":
            return datetime.fromisoformat(dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00")
        return dt
    # ISO fallback (some containers already ISO-like)
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

def maybe_quarantine(src: Path, reason: str, ingest_id: str, extra: Optional[str] = None) -> None:
    """Quarantine only in write-mode; log in dry-run."""
    if DRY_RUN:
        log(f"[DRY] QUARANTINE {src} -> {reason} ({extra or ''})")
        return
    q = quarantine_file(src, reason, ingest_id, extra=extra)
    if q:
        log(f"QUARANTINED {src} -> {q} ({reason})")
    else:
        log(f"QUARANTINE FAILED for {src} ({reason})")


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
    row.setdefault("added_at", now)   # only on insert
    row["updated_at"] = now           # ALWAYS refresh

    try:
        conn.execute(
            """
            INSERT INTO media (
            id, hash_sha256, phash, ext, bytes, taken_at, tz_offset,
            gps_lat, gps_lon, state, canonical_path, added_at, updated_at, xmp_written
            ) VALUES (
            :id, :hash_sha256, :phash, :ext, :bytes, :taken_at, :tz_offset,
            :gps_lat, :gps_lon, :state, :canonical_path, :added_at, :updated_at, :xmp_written
            )
            """,
            row,
        )
    except sqlite3.IntegrityError:
        conn.execute(
            """
            UPDATE media
            SET taken_at = COALESCE(media.taken_at, :taken_at),
                gps_lat  = COALESCE(media.gps_lat,  :gps_lat),
                gps_lon  = COALESCE(media.gps_lon,  :gps_lon),
                state    = CASE
                               WHEN media.state IN ('library','quarantine','deleted') THEN media.state
                               ELSE :state
                           END,
                canonical_path = COALESCE(:canonical_path, media.canonical_path),
                updated_at = :updated_at
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

def ingest_one_source(conn: sqlite3.Connection, source_label: str, staging_root: Path, note: Optional[str] = None):
    """Run a full ingest pass for one staging root."""
    if not staging_root.exists():
        log(f"SKIP {source_label}: path not found -> {staging_root}")
        return

    ingest_id = begin_ingest(conn, source_label, note)
    log(f"\n=== {source_label} ===")
    log(f"Started ingest batch: {ingest_id} ({staging_root})")

    scanned = moved = skipped_dupe = updated = quarantined = 0

    try:
        for root, dirs, files in os.walk(staging_root):
            # prune system dirs and AppleDouble dir entries
            dirs[:] = [d for d in dirs if d not in DIR_IGNORE and not d.startswith("._")]

            for name in files:
                p = Path(root) / name

                # junk files and AppleDouble resource forks
                if name in JUNK_FILES or any(name.startswith(pref) for pref in JUNK_PREFIXES):
                    if QUAR.get("junk", True):
                        maybe_quarantine(p, "junk", ingest_id, extra=("appledouble" if name.startswith("._") else "system_file"))
                        quarantined += 1
                    continue

                # unsupported extensions
                if not is_supported_media(p):
                    if QUAR.get("unsupported_ext", True):
                        maybe_quarantine(p, "unsupported_ext", ingest_id, extra=p.suffix.lower())
                        quarantined += 1
                    continue

                scanned += 1

                # per-file logic
                try:
                    size = p.stat().st_size
                except Exception as e:
                    if QUAR.get("stat_error", True):
                        maybe_quarantine(p, "stat_error", ingest_id, extra=str(e))
                        quarantined += 1
                    continue

                if size == 0:
                    if QUAR.get("zero_bytes", True):
                        maybe_quarantine(p, "zero_bytes", ingest_id)
                        quarantined += 1
                    continue

                h = sha256_file(p)
                meta = exiftool_json(p)
                ext = p.suffix.lower()
                hint = last_meaningful_folder(p.parent)

                # -------- capture time resolution --------
                taken_at = resolve_taken_at(meta, name, ALLOW_FILENAME_DATES)
                if not taken_at:
                    reason = "no capture date (exif/qt" + ("/filename" if ALLOW_FILENAME_DATES else "") + ")"
                    if QUAR.get("missing_datetime", True):
                        maybe_quarantine(p, "missing_datetime", ingest_id, extra=reason)
                        quarantined += 1
                    else:
                        log(f"SKIP missing_datetime: {p} ({reason})")
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
                }

                mid, current_state, current_canon = upsert_media(conn, media_row)
                insert_sighting(conn, mid, p, name, source_label, hint, ingest_id)

                # already in library
                if already_finalized(conn, h):
                    skipped_dupe += 1
                    log(f"= DUP in library: {p} ({h[:8]})")
                    if QUAR.get("dupes", True):
                        maybe_quarantine(p, "duplicate_in_library", ingest_id)
                        quarantined += 1
                    continue

                if current_state in ("review", "library") and current_canon:
                    try:
                        canon_missing = not Path(current_canon).exists()
                    except Exception:
                        canon_missing = True

                    if not canon_missing:
                        updated += 1
                        now = datetime.utcnow().isoformat()
                        conn.execute(
                            "UPDATE media SET last_verified_at=?, updated_at=? WHERE id=?",
                            (now, now, mid),
                        )
                        log(f"= Already tracked: state={current_state} path={current_canon}")
                        continue

                    if current_state == "review":
                        log(f"! Missing on disk (review); will requeue -> {current_canon}")
                    else:
                        log(f"! Missing on disk (library); leaving for reconcile -> {current_canon}")
                        skipped_dupe += 1
                        continue

                fname = canonical_name(taken_at, h, ext)
                dest = plan_nonclobber(REVIEW_ROOT, fname)

                if DRY_RUN:
                    log(f"[DRY] MOVE {p} -> {dest}")
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
                                maybe_quarantine(p, "move_failed", ingest_id, extra=f"{e1} | {e2}")
                                quarantined += 1

                    if moved_ok:
                        now = datetime.utcnow().isoformat()
                        conn.execute(
                            "UPDATE media SET state='review', canonical_path=?, updated_at=?, last_verified_at=? WHERE id=?",
                            (str(dest), now, now, mid),
                        )
                        moved += 1

                conn.commit()

        log(f"Summary {source_label}: scanned={scanned}, moved={'(dry)' if DRY_RUN else moved}, updated={updated}, skipped_dupe={skipped_dupe}, quarantined={quarantined}")

    finally:
        finish_ingest(conn, ingest_id)


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

    args = parser.parse_args()

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
    ensure_column(conn, "sightings", "folder_hint", "TEXT")
    ensure_column(conn, "sightings", "ingest_id", "TEXT")

    wanted = set(args.sources)  # labels like 'pc', 'other', etc.
    for label, path in STAGING_SOURCES.items():
        short = label.split("/", 1)[-1]  # 'pc' from 'Staging/pc'
        if wanted and short not in wanted and label not in wanted:
            continue
        ingest_one_source(conn, label, path, note=args.note)

    conn.close()

    elapsed = time.perf_counter() - t0
    log(f"\n=== Ingest complete. Total time: {elapsed:.1f} seconds ===")

    # quick peek at review queue
    conn2 = open_db()
    try:
        rows = conn2.execute("SELECT id, canonical_path, taken_at FROM v_review_queue LIMIT 10").fetchall()
        if rows:
            log("\nSample Review queue:")
            for rid, path, t in rows:
                log(f"  id={rid[:8]}… taken_at={t} path={path}")
    except sqlite3.Error as e:
        log(f"(note) could not read v_review_queue: {e}")
    finally:
        conn2.close()


if __name__ == "__main__":
    main()
