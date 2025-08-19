# Context: Pixarr ingest (working notes)

*Last updated: **today***

This doc summarizes the current behavior, decisions, and open threads from our recent work on the Pixarr ingest pipeline, so a future assistant (or dev) can pick up right away.

## What Pixarr does (scope here)

* Walks `media/Staging/*`, normalizes metadata, and:

  * **Good items** → `media/Review/` with canonical names; `state='review'`.
  * **Problem items** → `media/Quarantine/<reason>/`; `state='quarantine'`.
* Writes **all** actions to SQLite for audit, dedupe, and future tools.

### Review layout decision

* Keep **Review flat** during cleanup (no year/month).
* Year/month bucketing happens later at **final archive** time.

---

## Dedupe + hashing (current policy)

* **Hash:** SHA-256 of the entire file (all bytes, including EXIF).
* Therefore, “dupes” in our context = **bit-identical** files (same image **and** same EXIF/headers).
* We discussed “perpetual hashing” for items **under Review** to compare against Library and auto-dedupe; a ticket was drafted for this (see “Open threads”).

---

## Date resolution (capture time)

Order of precedence:

1. **EXIF/QuickTime** capture tags (strict).

   * Sentinel/invalid timestamps are ignored: `0000…`, `0001…`, `1970-01-01…`.
2. **Filename-derived** date (only if `--allow-filename-dates`).
3. (Optional) **File dates** (`ModifyDate/FileModifyDate`) if `--allow-file-dates`.

If no usable date → **`missing_datetime`** quarantine.

---

## Quarantine reasons (semantics)

* `missing_datetime` — media parsed OK but no reliable capture date.
* `junk` — `.DS_Store`, `Thumbs.db`, AppleDouble `._*`, etc.
* `unsupported_ext` — truly non-media types (e.g., `.pdf`).
* `zero_bytes` — 0-byte file with supported suffix.
* `stat_error` — can’t stat/read (dangling symlink, perms, I/O).
* `move_failed` — attempted move/copy failed.
* `dupes` / `duplicate_in_library` — policy-driven handling if already in Library.

> **Important change:** We now screen media by **suffix only** with `is_media_candidate(p)`.
> Health issues (e.g., broken symlink) are handled later and become **`stat_error`** instead of being mislabeled as unsupported.

---

## Canonical filenames

`YYYY-MM-DD_HH-MM-SS_<hash8><ext>`

* `<hash8>` = first 8 chars of SHA-256.
* Collisions resolved with `_2`, `_3`, … (no clobber).

---

## Logging (finalized behavior)

We implemented a console/file **matrix** plus a max-level filter for `-q`.

* `-q` → **console silent**; **file = INFO only** (WARNINGS suppressed in file)
* *default* → console **INFO only**; **file = INFO + WARNING**
* `-v` → console **INFO + WARNING**; **file = INFO + WARNING**
* `-vv` → console **DEBUG/INFO/WARNING**; **file = DEBUG/INFO/WARNING**
* `--log-level=X` → **both console and file use exactly X** (overrides matrix; no extra filters)

Notes:

* Quarantine events log at **WARNING**. In dry-run they add a `[DRY]` prefix but stay WARNING; visibility is controlled entirely by the matrix/level.
* Logs rotate nightly; keep 14.

---

## Test fixture (“zoo”)

`scripts/make_test_zoo.sh` synthesizes a tiny dataset under `Staging/other/_testcase_zoo` containing:

* Valid **EXIF JPEG** (good)
* Valid **MP4** w/ QuickTime CreateDate (good)
* **Duplicate** JPEG (same SHA-256)
* **Filename-timestamp** JPEG without EXIF (→ `missing_datetime` unless `--allow-filename-dates`)
* **PNG/GIF** screenshots (→ `missing_datetime`)
* **Zero-byte** `.heic` (→ `zero_bytes`)
* **Broken symlink** `.mov` (→ `stat_error`)
* **Unsupported** type `.pdf` (→ `unsupported_ext`)
* **Junk**: `.DS_Store`, AppleDouble `._*`, `Thumbs.db`

Dependencies: `exiftool`, `ffmpeg`, `Pillow` (installed via `requirements.txt`).
Script ensures EXIF/QuickTime tags are written so the “good” cases actually pass.

---

## CLI quick refs

```bash
# Dry-run, deep debug
python scripts/ingest_pass.py other -vv --data-dir /path/to/data

# Write mode
python scripts/ingest_pass.py other --write -v --data-dir /path/to/data

# Allow filename fallback
python scripts/ingest_pass.py other --allow-filename-dates -v --data-dir /path/to/data

# Quiet console, still log (INFO only in file with -q)
python scripts/ingest_pass.py other -q -vv --data-dir /path/to/data
```

---

## DB touchpoints (short)

* `media.hash_sha256` is the dedupe anchor; `state` ∈ `review|library|quarantine|deleted`.
* `media.quarantine_reason` set only when `state='quarantine'`.
* `sightings` tracks each path encounter + `ingest_id`.
* `ingests` rows per batch; script logs examples at end.

---

## Decisions we made (so future chat agents know)

1. **Review remains flat** until final archive (year/month later).
2. **Dupes** are binary (same SHA-256). No EXIF-merging on first pass.
3. **Quarantine** is part of normal flow; not all quarantines are “errors.”
4. **Dry-run and write** log at the **same severities**; only difference is side-effects.
5. **Logging matrix** above is the source of truth; `--log-level` hard-overrides it.

---

## Open threads / next items

* **Perpetual hashing of Review items** to auto-compare with Library (reduce dupes early). *(ticket drafted)*
* **Persisted/batched exiftool** to speed up metadata reads.
* **Reconcile job** to check `canonical_path` existence and requeue/mark deleted.
* **Near-dup pHash** for visually similar assets.
* **XMP writer** (post-finalize).
* **Parallelism** (IO-bound; needs careful DB contention handling).

---

## Where to look next

* Code: `scripts/ingest_pass.py` (see `maybe_quarantine`, logging setup, `_DATE_KEYS` assembly)
* Config: `pixarr.toml` vs CLI flags (CLI wins)
* Docs: `DEV_NOTES.md` for full details and handy commands
* Fixture: `scripts/make_test_zoo.sh` to reproduce edge cases quickly

---
