#!/usr/bin/env bash
set -euo pipefail
rm -f data/db/app.sqlite3 data/db/app.sqlite3-wal data/db/app.sqlite3-shm
echo "DB deleted. Run ingest_pass.py again to re-init."
