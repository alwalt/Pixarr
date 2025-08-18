# Pixarr

Lightweight ingest for personal photos/videos (like Radarr/Sonarr but for memories).
Scans `data/media/Staging/*`, derives a capture time, **renames** into a canonical form, and moves items to `Review/`.
**Note:** We do **not** modify EXIF yet—see the Roadmap.

## Folder layout

```
data/
  db/app.sqlite3
  media/
    Staging/{pc,icloud,sdcard,other}
    Review/
    Library/
    Quarantine/
```

## Install

```bash
python -m pip install -r requirements.txt   # if you’re tracking deps
# macOS: install exiftool
brew install exiftool
```

## Config (optional)

Create `pixarr.toml` (or copy from `pixarr.example.toml`):

```toml
[paths]
data_dir = "/Volumes/Data/Pixarr/data"

[ingest]
dry_run_default = true
allow_filename_dates = false
allow_file_dates = false

[quarantine]
missing_datetime = true
junk = true
unsupported_ext = true
zero_bytes = true
stat_error = true
move_failed = true
dupes = true
```

CLI flags override the config for that run.

## Run

Dry run (default):

```bash
python scripts/ingest_pass.py other -n "first dry scan"
```

Allow filename-derived timestamps:

```bash
python scripts/ingest_pass.py other --allow-filename-dates
```

Write mode:

```bash
python scripts/ingest_pass.py other --write
```

## Naming policy

* Canonical filename: `YYYY-MM-DD_HH-MM-SS_<hash8>.<ext>`
* Timestamp source order:

  1. **EXIF/QuickTime** capture dates
  2. **(optional)** filename-derived dates (`--allow-filename-dates`)
  3. File dates are off by default; enable with `--allow-file-dates`

## Quarantine policy

Config-driven (`[quarantine]` in TOML). Typical buckets:

* `missing_datetime`, `junk`, `unsupported_ext`, `zero_bytes`, `stat_error`, `move_failed`, `duplicate_in_library`.

## Roadmap / Action items

* **Write EXIF/XMP during Review → Library promotion.**

  * Backfill `DateTimeOriginal` / QuickTime creation atom from the canonical filename when EXIF is missing or wrong.
  * Persist timezone offsets and corrections.
* iCloud imports (icloudpd) and monitoring.
* Reconcile: handle files moved/deleted from `Library/`.
* Logging to file.
* Post-ingest cleanup (e.g., move processed/ignored files).
* Correction pipeline for anything with **no EXIF**, **no QuickTime date**, **filename-derived**, or **mtime fallback**.

