# Pixarr – Dev Notes

## What this project does

Stage → Review ingest for photos/videos. Primary timestamp comes from EXIF/QuickTime; optional fallbacks (filename or file modify dates) are opt-in. All actions recorded in SQLite; quarantine rules come from `pixarr.toml`.

## Runtime requirements

* Python **3.9+** (you’re on 3.9.6)
* `exiftool` on PATH
* Python deps: `tomli` (for 3.9/3.10), stdlib modules only otherwise

## Repository layout (target refactor)

```
pixarr/
  __init__.py
  cli.py                  # entry point (argparse, setup_logging, wiring)
  ingest.py               # ingest_one_source + high-level loop
  exif.py                 # exiftool_json, EXIF date parsing, filename parsing
  fs.py                   # hashing, path helpers, canonical_name, plan_nonclobber
  db/
    __init__.py
    dao.py                # begin_ingest, finish_ingest, upsert_media, sightings
    schema.sql            # SQLite schema (already in repo root/db/schema.sql)
  config.py               # load_config(), build_quarantine_cfg()
  logging_setup.py        # setup_logging(), logger adapters/filters
  constants.py            # SUPPORTED_EXT, JUNK lists, DIR_IGNORE, etc.
scripts/
  ingest_pass.py          # thin wrapper calling pixarr.cli:main()
```

> You currently have everything in `scripts/ingest_pass.py`. As you refactor, copy functions into the modules above and keep `scripts/ingest_pass.py` as a 20–30-line wrapper that calls `pixarr.cli:main()`.

## Data & directories

```
<DATA_DIR>/db/app.sqlite3
<DATA_DIR>/media/Staging/{pc,other,icloud,sdcard}
<DATA_DIR>/media/Review
<DATA_DIR>/media/Library
<DATA_DIR>/media/Quarantine/<reason>/
<DATA_DIR>/logs/pixarr-YYYYmmdd_HHMMSS.log
```

## Config

* File: `pixarr.toml` in repo root (copy from `pixarr.example.toml`).
* Sections:

  * `[paths] data_dir = "<absolute or relative>"`
  * `[ingest] dry_run_default = true|false, allow_filename_dates = true|false, allow_file_dates = true|false`
  * `[quarantine] junk=true, unsupported_ext=true, zero_bytes=true, stat_error=true, move_failed=true, dupes=true, missing_datetime=true`

**Precedence:** CLI flags override TOML; TOML overrides built-ins.

## CLI (common flags)

* `--write` (otherwise dry-run)
* `--allow-filename-dates`
* `--allow-file-dates`
* `--data-dir PATH`
* `--log-level=DEBUG|INFO|...` (console only)
* `--verbose` (`-vv` bumps file log to DEBUG)
* `--json-logs`
* `--heartbeat N`
* Sources:

  * `pc`, `other`, `icloud`, `sdcard`
  * Subdirs like `other/trip1`
  * Absolute/relative paths

**Examples**

```
python scripts/ingest_pass.py other -n "first dry scan"
python scripts/ingest_pass.py pc/loose --allow-filename-dates --log-level=DEBUG
python scripts/ingest_pass.py other --write -v
```

## Logging

* Console: human text at level from `--log-level` or `-v`/`-q`
* File: `TimedRotatingFileHandler` → rotates at local midnight, keeps 14 days

  * Level: `INFO` by default; `DEBUG` if `-vv` or higher
  * Format: `YYYY-mm-ddTHH:MM:SSZ [LEVEL] [source:ingest_id] message`
* To see **successful moves in dry-run**, use `--log-level=DEBUG` (or `-vv`).

**Clean logs script**

```
find "<DATA_DIR>/logs" -type f -name "pixarr-*.log" -delete
```

## Database

* `media` holds one row per content hash (with `taken_at`, `canonical_path`, etc.)
* `sightings` records every file seen (path, source, ingest\_id)
* `ingests` tracks batches (id, source, start/finish, note)
* Optional view: `v_review_queue` (helpful peek after runs)

## Ingest decision tree (resolve\_taken\_at)

1. EXIF/QuickTime date keys (`DateTimeOriginal`, `CreateDate`, etc.; `ModifyDate` only if flag)
2. Filename-derived timestamp (only if `--allow-filename-dates`)
3. Otherwise: quarantine `missing_datetime` (if enabled)

> **Action item:** File renames don’t fix EXIF. You’ll later run a writeback step (e.g., `exiftool -DateTimeOriginal=...`) to set metadata based on the canonical name or DB.

## Quarantine

Reasons include: `junk`, `unsupported_ext`, `zero_bytes`, `stat_error`, `move_failed`, `dupes`, `missing_datetime`. All toggled via `[quarantine]` in TOML.

## Troubleshooting

* **No EXIF:** expect `missing_datetime` quarantines unless `--allow-filename-dates`.
* **No log lines for moves in file:** add `-vv` (file handler becomes DEBUG).
* **exiftool not found:** install via Homebrew: `brew install exiftool`.

## Quick “start new chat” context block

Paste this at the start of a new session so help stays precise:

```
Project: Pixarr (Python 3.9.6)
Purpose: Stage → Review ingest; EXIF/QuickTime first; optional filename/file-date fallbacks; quarantine via pixarr.toml.
Key deps: exiftool CLI, sqlite3 (WAL), tomli, logging w/ TimedRotatingFileHandler.
Layout (target): pixarr/{cli.py, ingest.py, exif.py, fs.py, db/dao.py, logging_setup.py, config.py, constants.py}
Data dirs: <DATA_DIR>/{db,media/{Staging/*,Review,Library,Quarantine},logs}
Config: pixarr.toml -> [ingest], [quarantine], [paths]
Flags I use: --write, --allow-filename-dates, --allow-file-dates, --heartbeat, --log-level=DEBUG
Current task: <fill in>
```
