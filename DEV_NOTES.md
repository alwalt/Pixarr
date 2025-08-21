# DEV\_NOTES.md

## 1) What this project is

Pixarr ingests messy photo/video folders from `media/Staging/*`, normalizes metadata, and either:

* moves good items into `media/Review/` (`state='review'`) with canonical filenames, or
* sends problem items to `media/Quarantine/<reason>/` (`state='quarantine'`) with a recorded reason.

All actions are written to SQLite for auditing, dedupe, and future tooling.

---

## 2) Repo layout (most-touched code)

```
db/schema.sql                # canonical DB schema
pixarr.example.toml          # example config
scripts/
  ingest_pass.py             # main ingest script
  init_db.py                 # create DB from schema
  last_ingests.py            # show recent batches
  last_media.py              # show recent media rows
  pixarr_db.py               # simple DB utilities (legacy; keep for now)
  pixarr_query.py            # read-only CLI for DB (states, reasons, sightings, batches)
  reset_db.sh                # nuke & re-init DB (dev only)
  make_test_zoo.sh           # synthesize a small "good + bad" test set (legit JPEG/MP4 timestamps)
tests/
  test_taken_resolver.py     # unit test for filename-date parsing
```

---

## 3) Runtime filesystem layout (under `--data-dir`, default `./data`)

```
data/
  db/app.sqlite3
  logs/pixarr-YYYYmmdd_HHMMSS.log
  media/
    Staging/
      pc/
      other/
      icloud/
      sdcard/
      ...
    Review/                 # good items land here during ingest
    Library/                # finalized assets (out of scope for ingest_pass.py)
    Quarantine/
      duplicate/            # ← unified bucket for ALL duplicate reasons (see §“Quarantine & Review — TL;DR”)
      missing_datetime/
      stat_error/
      move_failed/
      zero_bytes/
      unsupported_ext/
      junk/
```

---

## 4) Quick start

```bash
# 1) Dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # includes Pillow; install imageio-ffmpeg for the zoo script
# exiftool must be on PATH for ingest (metadata read); e.g., macOS: brew install exiftool

# 2) Initialize DB
python scripts/init_db.py --data-dir /Volumes/Data/Pixarr/data

# 3) Dry run (no moves), verbose to console + file log
python scripts/ingest_pass.py other -vv --data-dir /Volumes/Data/Pixarr/data

# 4) Actually move files to Review/ and Quarantine/
python scripts/ingest_pass.py other --write -v --data-dir /Volumes/Data/Pixarr/data
```

Useful variants:

```bash
# Console silent, still log to file
python scripts/ingest_pass.py other -vv --quiet --data-dir /Volumes/Data/Pixarr/data

# JSON logs (machine-friendly)
python scripts/ingest_pass.py other --json-logs -vv --quiet --data-dir /Volumes/Data/Pixarr/data

# Allow filename-derived dates
python scripts/ingest_pass.py other --allow-filename-dates -v --data-dir /Volumes/Data/Pixarr/data

# Progress heartbeat every 200 files
python scripts/ingest_pass.py other --heartbeat 200 -v --data-dir /Volumes/Data/Pixarr/data
```

---

## 5) Configuration (`pixarr.toml`)

Copy `pixarr.example.toml` → `pixarr.toml` at repo root. Key sections:

* `[paths]` → `data_dir`
* `[ingest]` → `dry_run_default`, `allow_file_dates`, `allow_filename_dates`, `on_review_dupe`
* `[quarantine]` → toggles per reason (`junk`, `unsupported_ext`, `zero_bytes`, `stat_error`, `move_failed`, `dupes`, `missing_datetime`)

CLI flags always override config.

---

## 6) Logging (final behavior)

* **Console level** is controlled by `-v/--verbose`, `-q/--quiet`, or `--log-level`.
* **File logs** go to `<data_dir>/logs/pixarr-YYYYmmdd_HHMMSS.log`, rotate nightly (keep 14), `--json-logs` supported.

**Matrix:**

* `-q` → console silent; **file = INFO only** (warnings are suppressed in file via max-level filter)
* default (no flags) → console **INFO only**; **file = INFO + WARNING**
* `-v` → console **INFO + WARNING**; **file = INFO + WARNING**
* `-vv` → console **DEBUG/INFO/WARNING**; **file = DEBUG/INFO/WARNING**
* `--log-level=X` → **both console and file use exactly X** (overrides the matrix; no extra filters)

**Quarantine events** are logged at **WARNING**. In dry-runs they include a `[DRY]` prefix but keep the same level; visibility is controlled by the matrix above.

To prune old logs (example: >30 days):

```bash
find /Volumes/Data/Pixarr/data/logs -type f -name 'pixarr-*.log' -mtime +30 -delete
```

---

## 7) Ingest algorithm (high level)

For each file under selected Staging roots:

1. **Junk/system** entries quarantined if enabled.
2. **Extension screen** (see §8): accept only media **candidates** by suffix; true non-media (e.g., `.pdf`) → `unsupported_ext`.
3. `p.stat()` for size/health:

   * 0 bytes → `zero_bytes`
   * stat/IO/symlink failure → `stat_error`
4. Compute **SHA-256** (dedupe anchor).
5. Read metadata via `exiftool -j -n`.
6. Resolve `taken_at`:

   * EXIF/QuickTime keys only (strict). Sentinel/invalid timestamps ignored (`0000…`, `0001…`, `1970-01-01…`).
   * Optional filename fallback (`--allow-filename-dates`).
   * Optional file-date fallback (`--allow-file-dates`) when flag is given (wired via `_DATE_KEYS`).
7. If no usable `taken_at` → `state='quarantine'`, `quarantine_reason='missing_datetime'`. In write-mode, file is moved under `Quarantine/missing_datetime/`; always recorded in DB.
8. If good → upsert `media` (`state='review'`), add `sightings`, compute canonical name `YYYY-MM-DD_HH-MM-SS_<hash8>.<ext>`, and move to `Review/` (or log `[DRY] MOVE`).
9. **Dupes handling (streamlined):**

   * **File-level dupes** (same `hash_sha256`) are detected across Review/Library.
   * **Content dupes** (same pixels, different bytes) are supported by `content_sha256` **for decodable image formats** (e.g., JPEG/PNG).
   * All duplicate sources are routed to **`Quarantine/duplicate/`** with a sidecar JSON noting `reason` and `extra` (basis and target id).
10. Summarize counts; log batches and examples.

> **Note:** We are **not** computing content hashes for **HEIC/HEIF** right now. File-level dupes still work for HEIC; content-dup analysis applies to formats we decode (e.g., JPEG/PNG).

---

## 8) “Media candidate” vs `stat_error` (important change)

We replaced `is_supported_media(p)` (extension **and** health) with **`is_media_candidate(p)`** (extension **only**):

```python
def is_media_candidate(p: Path) -> bool:
    """Screen by extension only; actual validity decided by stat()/EXIF later."""
    return p.suffix.lower() in SUPPORTED_EXT

# In the loop
if not is_media_candidate(p):
    if QUAR.get("unsupported_ext", True):
        stats["q_counts"]["unsupported_ext"] += 1
        maybe_quarantine(p, "unsupported_ext", ingest_id, extra=p.suffix.lower())
        stats["quarantined"] += 1
    continue

try:
    size = p.stat().st_size
except Exception as e:
    if QUAR.get("stat_error", True):
        stats["q_counts"]["stat_error"] += 1
        maybe_quarantine(p, "stat_error", ingest_id, extra=str(e))
        stats["quarantined"] += 1
    continue
```

**Why:** files that *look* like media (e.g., `broken_symlink.mov`, unreadable `.heic`) should flow into media handling and become `stat_error`, not be dropped as `unsupported_ext`.

**Semantics:**

* `unsupported_ext` → truly unsupported type (e.g., `.pdf`)
* `stat_error` → can’t stat/read (dangling symlink, perms, IO)
* `zero_bytes` → 0-byte with supported suffix
* `missing_datetime` → parsed OK, no capture time

**Metrics:** “scanned” counts only candidate media (by suffix). Quarantine totals include all reasons (junk, unsupported, etc.), so `quarantined` can exceed `scanned`.

*Edge idea:* if you want a dedicated `symlink_error`, detect `p.is_symlink()` prior to `stat()`.

---

## 9) Canonical filenames

`YYYY-MM-DD_HH-MM-SS_<hash8><ext>`

* `taken_at` provides the timestamp.
* `<hash8>` is the first 8 chars of SHA-256.
* Name collisions resolved with `_2`, `_3`, … (`plan_nonclobber`).

---

## 10) DB schema (key pieces)

**media**

* `id` (UUID from sha256), `hash_sha256` (unique), **`content_sha256` (nullable; decoded-pixels digest for images)**, `ext`, `bytes`
* `taken_at`, `tz_offset`, `gps_lat`, `gps_lon`
* `state` ∈ `('staging','review','library','quarantine','deleted')`
* `canonical_path`
* `quarantine_reason` (TEXT; set for quarantined; cleared otherwise)
* `added_at`, `updated_at`, `last_verified_at`, `deleted_at`, `xmp_written`

**sightings**

* `media_id`, `source_root`, `full_path`, `filename`, `folder_hint`, `ingest_id`, `seen_at`

**ingests**

* batches → `id`, `source`, `started_at`, `finished_at`, `notes`

**View for content dupes**

* `v_duplicate_content` groups by `content_sha256` and surfaces clusters with `COUNT(*) > 1`.

See `db/schema.sql` for full DDL and views (`v_review_queue`, `v_needs_xmp`, `v_deleted`, `v_duplicate_content`).

---

## 11) DB migrations (dev)

If your DB predates these columns:

**Option A (dev only):**

```bash
bash scripts/reset_db.sh  # WARNING: wipes data/db/app.sqlite3
```

**Option B (manual migrate):**

```sql
-- existing earlier migration
ALTER TABLE media ADD COLUMN quarantine_reason TEXT;

-- NEW: content hash support
ALTER TABLE media ADD COLUMN content_sha256 TEXT;
CREATE INDEX IF NOT EXISTS idx_media_content_hash ON media(content_sha256);

-- keep these common indexes
CREATE INDEX IF NOT EXISTS idx_media_state    ON media(state);
CREATE INDEX IF NOT EXISTS idx_media_taken_at ON media(taken_at);

-- (re)create the analysis view
DROP VIEW IF EXISTS v_duplicate_content;
CREATE VIEW v_duplicate_content AS
SELECT
  content_sha256,
  COUNT(*)        AS count_rows,
  MIN(added_at)   AS first_seen,
  MAX(updated_at) AS last_seen
FROM media
WHERE content_sha256 IS NOT NULL
GROUP BY content_sha256
HAVING COUNT(*) > 1;
```

The ingest script auto-adds safe columns on `sightings` (`folder_hint`, `ingest_id`).

---

## 12) DB inspection CLI (`pixarr_query.py`)

General form:

```bash
python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data <subcommand> [options...]
```

Subcommands:

* `states [--where "..."]` – counts by `media.state`
* `reasons` – histogram of `quarantine_reason` where `state='quarantine'`
* `quarantine [--unmoved-only] [--reason R] [--hours N|--since ISO] [--limit N]` – quarantined rows (with original filenames)
* `sightings [--like PAT] [--ingest-id UUID] [--media-id ID] [--hours N|--since ISO] [--limit N]`
* `batches [--limit N]` – recent ingest batches

---

## 13) Troubleshooting

* **`exiftool` not found** → install it and re-run (`brew install exiftool` on macOS).
* **Invalid EXIF sentinel** → `_parse_exif_dt` ignores `0000…/0001…/1970…`; update `ingest_pass.py` if you still see errors.
* **Don’t see `[DRY] MOVE` in file logs** → bump to `-vv` or `--log-level=DEBUG`. Logs are in `<data_dir>/logs/`.
* **Frequent `missing_datetime`** → try `--allow-filename-dates`. If still missing, timestamps are truly absent; curate in quarantine.

---

## 14) Performance knobs

* Hash buffer: 1 MB chunks (`sha256_file`)
* Heartbeat: `--heartbeat N` or `PIXARR_HEARTBEAT`
* `exiftool` is per-file; future: batch or persistent process
* No concurrency yet (IO-bound; DB contention needs care)

---

## 15) Testing

```bash
pytest -q
# or
python -m pytest -q
```

* `tests/test_taken_resolver.py` covers filename → datetime parsing.
* Add tests around `_parse_exif_dt`, quarantine routing, canonical name collisions when convenient.

---

## 16) Coding conventions

* Python ≥3.9; prefer stdlib + small helpers.
* **Logging:** use global `LOGGER` and `batch_logger(ingest_id, source)` when you need context.

  * INFO = actions/summaries; WARNING/ERROR = quarantines/failures; DEBUG = per-file details.
* **DB:** `hash_sha256` is the dedupe anchor. Mutate rows through `upsert_media`, `insert_sighting`. Keep state transitions explicit.

---

## 17) Roadmap (nice-to-haves)

* Refactor into a `pixarr/` package (config, logging, db, exif, ingest, cli)
* XMP writer post-finalize
* Reconcile job for `canonical_path` existence
* Perceptual hash (pHash) for near-duplicates
* React UI (grids → triage → tagging/search)
* Importers (iCloud, Takeout, SD, WhatsApp)
* Persisted/batched exiftool
* Metrics (throughput, quarantine rate, reasons)
* **Content hashing for HEIC/HEIF** (future; file-hash dupes already supported)

---

## 18) Handy commands

```bash
# Tail logs live
tail -f /Volumes/Data/Pixarr/data/logs/pixarr-*.log

# Count files by state
python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data states

# Quarantine reasons histogram
python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data reasons

# Quarantine, unmoved (dry-run)
python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data quarantine --unmoved-only --limit 100

# Sightings by pattern
python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data sightings --like 'IMG_07%' --limit 100

# Inspect content-dup clusters
sqlite3 /Volumes/Data/Pixarr/data/db/app.sqlite3 "SELECT * FROM v_duplicate_content ORDER BY last_seen DESC LIMIT 20;"
```

---

## 19) Test fixture (“zoo”)

Use `scripts/make_test_zoo.sh` to synthesize a small set of good/bad files in `Staging/other/_testcase_zoo`.
This version creates **legit timestamps** in both the JPEG and MP4 without depending on system ffmpeg:

* **JPEG (good)** – `DateTimeOriginal` + `CreateDate` written at save time (Pillow).
* **MP4 (good)** – `creation_time` baked at encode time using **Python-managed ffmpeg** (`imageio-ffmpeg`).
* **Duplicate of JPEG** – tests dupe path/update behavior.
* **Filename-timestamp JPEG (no EXIF)** – `missing_datetime` (unless `--allow-filename-dates`).
* **PNG/GIF** – typically `missing_datetime`.
* **Zero-byte `.heic`** – `zero_bytes`.
* **Broken symlink `.mov`** – `stat_error`.
* **`.pdf`** – `unsupported_ext`.
* **Junk** – `.DS_Store`, `._junk.bin`, `Thumbs.db`.

Quick runs:

```bash
# Create the zoo (requires: pip install Pillow imageio-ffmpeg)
scripts/make_test_zoo.sh --data-dir /Volumes/Data/Pixarr/data

# Ingest
python scripts/ingest_pass.py Staging/other/_testcase_zoo -v --data-dir /Volumes/Data/Pixarr/data
python scripts/ingest_pass.py Staging/other/_testcase_zoo --allow-filename-dates -v --data-dir /Volumes/Data/Pixarr/data
python scripts/ingest_pass.py Staging/other/_testcase_zoo -vv --data-dir /Volumes/Data/Pixarr/data
```

---

## Quarantine & Review — TL;DR

**What we do**

* Files under `Staging/*` are screened by **suffix only** (`is_media_candidate`).
* Health is checked via `stat()` and EXIF/QuickTime; good items go to **Review/**, problems go to **Quarantine/<reason>/** (write-mode) and are logged either way.

**Reasons & semantics**

* `junk` — system clutter (e.g., `.DS_Store`, `Thumbs.db`, `._*`). *Not inserted into DB.*
* `unsupported_ext` — truly non-media types (e.g., `.pdf`). *Not inserted into DB.*
* `stat_error` — can’t `stat()`/read (dangling symlink, perms, I/O).
* `zero_bytes` — 0-byte with a supported suffix.
* `missing_datetime` — no usable capture time from EXIF/QuickTime (filename only if `--allow-filename-dates`).
* `move_failed` — couldn’t move/copy to Review.
* `duplicate_in_library` — hash already finalized in Library.
* `duplicate_in_review` — hash already present in Review.
* `duplicate_content` — **pixels identical** to an existing item (metadata-only differences; supported for formats we decode, e.g., JPEG/PNG).
  **All duplicates** route to a **single folder**: `Quarantine/duplicate/`.

> **Sidecar JSON**: every quarantined file gets `<name>.quarantine.json` with:
>
> ```json
> {
>   "reason": "duplicate_in_review" | "duplicate_in_library" | "duplicate_content",
>   "extra":  "basis=file|content dupe_of=<media_id>",
>   "original_path": "...",
>   "quarantined_to": "...",
>   "ingest_id": "...",
>   "timestamp": "..."
> }
> ```
>
> Use this to see whether it was a **file** or **content** duplicate and which media row is canonical.

> **DB rule of thumb:** We insert media rows for *candidate media* when we can hash it (normal Review moves and quarantines like `stat_error`, `zero_bytes`, `missing_datetime`, `move_failed`, `duplicate_*`). We do **not** insert rows for `junk` / `unsupported_ext`.

**Duplicates in Review (configurable)**

* Policy is `ingest.on_review_dupe` in `pixarr.toml` (or `--on-review-dupe`):

  * `ignore` — mark existing row `last_verified_at`, leave source file alone (still logs a sighting).
  * `quarantine` *(default)* — move source to **`Quarantine/duplicate/`**.
  * `delete` — delete source (or quarantine as `move_failed` if delete fails).
* Library dupes always count as `duplicate_in_library` (and may be quarantined if `quarantine.dupes = true`).

**Logging (final behavior)**

* Quarantines are **WARNING**; dry-run uses the same level with a `[DRY]` prefix.
* Matrix:

  * `-q` → console silent; file **INFO only** (WARNING+ suppressed in file)
  * default → console **INFO only**; file **INFO+WARNING**
  * `-v` → console **INFO+WARNING**; file **INFO+WARNING**
  * `-vv` → console **DEBUG/INFO/WARNING**; file **DEBUG/INFO/WARNING**
  * `--log-level=X` → both handlers use **exactly X** (no extra filters)

**Config snippet**

```toml
[ingest]
dry_run_default = true
allow_filename_dates = false
allow_file_dates = false
on_review_dupe = "quarantine"  # or "ignore" | "delete"

[quarantine]
junk = true
unsupported_ext = true
zero_bytes = true
stat_error = true
move_failed = true
dupes = true
missing_datetime = true
```

**CLI examples**

```bash
# Prefer filename timestamps too
python scripts/ingest_pass.py other --allow-filename-dates -v

# Set review-dupe policy for this run only
python scripts/ingest_pass.py other --on-review-dupe=ignore -v
```

---

Tiny DB viewer

`scripts/show_media.py` prints one media row + related records.

Default DB resolution order (no --db):

* `PIXARR_DB` → file
* `PIXARR_DATA_DIR` → `db/app.sqlite3`
* `pixarr.toml [paths].data_dir` → `db/app.sqlite3`
* `./data/db/app.sqlite3` (repo) → `./data/db/app.sqlite3` (cwd)

Usage:

```
python scripts/show_media.py --id bb6d33dc-91f3-5dd1-bfc9-d5e2fc1024bd
python scripts/show_media.py --hash 7151bea9f6a5...
python scripts/show_media.py --hash-prefix 242053ae
python scripts/show_media.py --path "/Volumes/Data/Pixarr/data/media/Staging/other/_testcase_zoo/dupe_of_exif_ok.jpg"
PIXARR_DATA_DIR=/Volumes/Data/Pixarr/data python scripts/show_media.py --hash-prefix 242053ae
```

---

## Thumbnail Caching

To keep the React frontend responsive when browsing large folders of media, the backend generates and serves cached thumbnails.

* **Location:**
  All thumbnails are stored under `data/thumb-cache/`.
  File names are derived from the media file path (hashed/escaped to avoid collisions).

* **Creation:**

  * When the frontend requests an image preview (via `/preview` or `/thumbnail` routes), the backend checks if a cached thumbnail exists.
  * If not, it uses Pillow (Python Imaging Library) to generate a downsized JPEG/PNG and writes it to `thumb-cache`.
  * Subsequent requests serve the cached file directly for speed.

* **Cleanup:**

  * Thumbnails are **ephemeral**: they can always be regenerated from the original media.
  * You can safely delete the `thumb-cache/` folder at any time; the system will lazily rebuild missing thumbnails on demand.
  * In production, you may want a cronjob or maintenance script to prune very old cache entries.

* **Git Ignore:**
  Since cached thumbnails are artifacts, **they should not be version-controlled**. Ensure the following line is in `.gitignore`:

  ```
  # Thumbnail cache (safe to delete)
  data/thumb-cache/
  ```

---