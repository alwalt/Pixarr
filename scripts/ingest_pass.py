#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pixarr — ingest pass over Staging subfolders.

Changes in this build:
- EXIF-only: taken_at comes ONLY from EXIF/QuickTime datetime tags.
- Hard fail without exiftool.
- Files with no EXIF/QuickTime capture date are QUARANTINED to Quarantine/missing_datetime/.
- Dry-run by default; use --write to actually move files to Review/.

Requirements:
- Python 3.10+
- exiftool on PATH (hard requirement)
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
DRY_RUN = True  # default; overridden by --write

SUPPORTED_EXT = {
    ".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff", ".gif",
    ".mp4", ".mov", ".m4v", ".avi", ".webp",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2", ".orf", ".srw"
}
GENERIC_FOLDERS = {"dcim", "misc", "export", "photos", "images", "img", "camera", "mobile", "iphone", "android"}

JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_PREFIXES = {"._"}  # AppleDouble resource forks like ._IMG_1234.JPG
DIR_IGNORE = {".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems"}


# Quarantine toggles
QUARANTINE_JUNK         = True
QUARANTINE_UNSUPPORTED  = True
QUARANTINE_ZERO_BYTE    = True
QUARANTINE_STAT_ERROR   = True
QUARANTINE_MOVE_FAILURE = True
QUARANTINE_DUPES        = True  # dupes of existing library content
QUARANTINE_MISSING_DATETIME = True # NEW: quarantine when no EXIF/QuickTime capture date is present

# Fallback toggles (set via CLI flags in main())
ALLOW_FILENAME_DATES = False   # default; set from --allow-filename-dates

# ---------- Paths configured at runtime ----------
DATA_DIR: Path
DB_PATH: Path
REVIEW_ROOT: Path
QUARANTINE_ROOT: Path
STAGING_SOURCES: Dict[str, Path]
SCHEMA_PATH: Path

# ---------- exiftool (hard requirement) ----------
EXIFTOOL_PATH = shutil.which("exiftool")
if not EXIFTOOL_PATH:
    sys.stderr.write("FATAL: exiftool not found on PATH. Install it first (e.g., Homebrew: brew install exiftool).\n")
    sys.exit(1)

# ---------- Utilities ----------

def repo_root() -> Path:
    """Resolve repo root as folder containing this file's parent (Pixarr/)."""
    return Path(__file__).resolve().parents[1]

def log(msg: str) -> None:
    print(msg, flush=sys.stdout.isatty())

def ensure_dirs() -> None:
    (DATA_DIR / "db").mkdir(parents=True, exist_ok=True)
    for d in [
        "Staging/pc", "Staging/other", "Staging/icloud", "Staging/sdcard",
        "Review", "Library", "Quarantine",
        # subfolder for our missing-date quarantine class
        "Quarantine/missing_datetime",
        "Quarantine/junk",
        "Quarantine/unsupported_ext",
        "Quarantine/zero_bytes",
        "Quarantine/stat_error",
        "Quarantine/move_failed",
        "Quarantine/duplicate_in_library",
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

# EXIF-only date parsing
# Only capture/camera-origin dates. No filesystem dates.
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
    # ISO fallback (still EXIF-originated strings for some containers)
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
                    if QUARANTINE_JUNK:
                        maybe_quarantine(p, "junk", ingest_id, extra=("appledouble" if name.startswith("._") else "system_file"))
                        quarantined += 1
                    continue

                # unsupported extensions
                if not is_supported_media(p):
                    if QUARANTINE_UNSUPPORTED:
                        maybe_quarantine(p, "unsupported_ext", ingest_id, extra=p.suffix.lower())
                        quarantined += 1
                    continue

                scanned += 1

                # per-file logic
                try:
                    size = p.stat().st_size
                except Exception as e:
                    if QUARANTINE_STAT_ERROR:
                        maybe_quarantine(p, "stat_error", ingest_id, extra=str(e))
                        quarantined += 1
                    continue

                if size == 0:
                    if QUARANTINE_ZERO_BYTE:
                        maybe_quarantine(p, "zero_bytes", ingest_id)
                        quarantined += 1
                    continue

                h = sha256_file(p)
                meta = exiftool_json(p)
                ext = p.suffix.lower()
                hint = last_meaningful_folder(p.parent)

                # -------- capture time resolution --------
                # 1) EXIF/QuickTime tags (strict by default)
                taken_at = extract_taken_at_exif_only(meta)

                # 2) Optional filename-derived fallback (only if flag enabled)
                if not taken_at and ALLOW_FILENAME_DATES:
                    fn_dt = _taken_from_filename(name)  # make sure you added this helper
                    if fn_dt:
                        taken_at = fn_dt.isoformat()

                # 3) Still nothing? quarantine
                if not taken_at:
                    if QUARANTINE_MISSING_DATETIME:
                        extra_msg = "no capture date (exif/qt"
                        if ALLOW_FILENAME_DATES:
                            extra_msg += "/filename"
                        extra_msg += ")"
                        maybe_quarantine(p, "missing_datetime", ingest_id, extra=extra_msg)
                        quarantined += 1
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

                if already_finalized(conn, h):
                    skipped_dupe += 1
                    log(f"= DUP in library: {p} ({h[:8]})")
                    if QUARANTINE_DUPES:
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
                            if QUARANTINE_MOVE_FAILURE:
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
    parser = argparse.ArgumentParser(description="Pixarr: ingest media from staging folders (EXIF-only timestamps).")
    parser.add_argument("sources", nargs="*", help="Subset of sources to ingest (pc, other, icloud, sdcard)")
    parser.add_argument("-n", "--note", help="Optional note to attach to this ingest batch")
    parser.add_argument("--write", action="store_true", help="Perform moves/copies (default is dry-run)")
    parser.add_argument("--data-dir", default=str(repo_root() / "data"),
                        help="Root data directory (default: ./data under repo)")
    parser.add_argument("--allow-file-dates", action="store_true",
                        help="Allow ModifyDate/FileModifyDate as capture time fallback")
    parser.add_argument("--allow-filename-dates", action="store_true",
                        help="Allow filename-derived timestamps as fallback (e.g., PHOTO-YYYY-MM-DD-HH-MM-SS.jpg)")

    args = parser.parse_args()

    # Build date keys dynamically from the flags
    base_keys = [
        "DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate",
        "QuickTime:CreateDate", "QuickTime:CreationDate"
    ]
    if args.allow_file_dates:  # <-- underscores, not hyphen
        base_keys += ["ModifyDate", "FileModifyDate"]

    # Update the global used by extract_taken_at_exif_only()
    global _DATE_KEYS
    _DATE_KEYS = base_keys

    # Expose filename-dates flag to the ingest loop
    global ALLOW_FILENAME_DATES
    ALLOW_FILENAME_DATES = args.allow_filename_dates  # <-- underscores

    # Paths / bootstrap
    base = Path(args.data_dir).resolve()
    pathize(base)

    global DRY_RUN
    DRY_RUN = not args.write

    ensure_dirs()
    if not DB_PATH.exists():
        ensure_db()

    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)

    mode = "DRY-RUN" if DRY_RUN else "WRITE"
    log(f"Mode: {mode}")
    log(f"DATA_DIR = {DATA_DIR}")

    t0 = time.perf_counter()

    conn = open_db()
    ensure_column(conn, "sightings", "folder_hint", "TEXT")
    ensure_column(conn, "sightings", "ingest_id", "TEXT")

    wanted = set(args.sources)
    for label, path in STAGING_SOURCES.items():
        short = label.split("/", 1)[-1]
        if wanted and short not in wanted and label not in wanted:
            continue
        ingest_one_source(conn, label, path, note=args.note)

    conn.close()

    elapsed = time.perf_counter() - t0
    log(f"\n=== Ingest complete. Total time: {elapsed:.1f} seconds ===")

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
