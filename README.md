# Pixarr

**Pixarr** is a personal photo & video pipeline inspired by Radarr/Sonarr/Lidarr.
It ingests raw media from *Staging*, deduplicates by content hash, and moves files through a review → library workflow.

## Features

* Ingest from multiple staging sources (`pc`, `icloud`, `sdcard`, `other`)
* Deduplication via SHA-256 (one row per unique binary)
* Light EXIF parsing (timestamps, GPS, camera info)
* Quarantine junk, unsupported, zero-byte, or duplicate files
* SQLite database with full schema for provenance, tagging, and audit trails
* Dry-run by default; safe to test before committing moves

## Directory Structure

```
Pixarr/
├── scripts/ingest_pass.py   # main ingest script
├── db/schema.sql            # canonical DB schema
├── data/                    # created on first run (ignored in git)
│   ├── media/
│   │   ├── Staging/{pc,icloud,sdcard,other}
│   │   ├── Review/
│   │   ├── Library/
│   │   └── Quarantine/
│   └── db/app.sqlite3
```

## Quick Start

1. Install dependencies:

   * Python 3.10+
   * [ExifTool](https://exiftool.org/)

2. Run a dry ingest (default):

   ```bash
   python scripts/ingest_pass.py
   ```

3. Actually move files into **Review/**:

   ```bash
   python scripts/ingest_pass.py --write
   ```

4. Optional: point at a different storage location:

   ```bash
   python scripts/ingest_pass.py --data-dir "/Volumes/Data/Memories" --write
   ```

## Notes

* Files never move directly into `Library/` — only into `Review/`.
* Deduplication is based on SHA-256 stored in the database.
* Quarantined files are written to `Quarantine/<reason>/` with a JSON sidecar.
