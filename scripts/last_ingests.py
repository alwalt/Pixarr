#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
last_ingests.py — print the most recent Pixarr ingest batches from SQLite.

Usage (from repo root):
  python scripts/last_ingests.py                 # auto-detect DB via pixarr.toml or ./data/db/app.sqlite3
  python scripts/last_ingests.py -n 5            # show 5 latest ingests
  python scripts/last_ingests.py --data-dir /path/to/data
  python scripts/last_ingests.py --db /path/to/app.sqlite3

Options:
  -n, --limit N        How many rows to show (default: 2)
  --data-dir PATH      Pixarr data dir (uses <data-dir>/db/app.sqlite3)
  --db PATH            Direct path to the SQLite DB (overrides everything)

Notes:
  - No exiftool required; this only reads SQLite.
  - If you're on Python < 3.11 and see a TOML import error, install the backport:
      python -m pip install tomli
"""


import argparse
import sqlite3
from pathlib import Path
from typing import Optional
import tomli as toml

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(path: Path) -> dict:
    """Load pixarr.toml if present; return {} on any issue."""
    if not path.exists():
        return {}
    try:
        return toml.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def resolve_db_path(cli_db: Optional[str], cli_data_dir: Optional[str]) -> Path:
    if cli_db:
        return Path(cli_db).expanduser().resolve()
    if cli_data_dir:
        return Path(cli_data_dir).expanduser().resolve() / "db" / "app.sqlite3"

    cfg = load_config(repo_root() / "pixarr.toml")
    data_dir = cfg.get("paths", {}).get("data_dir")
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "db" / "app.sqlite3"

    return (repo_root() / "data" / "db" / "app.sqlite3").resolve()

def print_rows(rows) -> None:
    if not rows:
        print("No ingests found.")
        return
    headers = list(rows[0].keys())
    print("\t".join(headers))
    print("-" * 80)
    for r in rows:
        print("\t".join("" if r[h] is None else str(r[h]) for h in headers))

def main():
    ap = argparse.ArgumentParser(description="Show the most recent Pixarr ingest batches.")
    ap.add_argument("-n", "--limit", type=int, default=2, help="How many most recent ingests to show (default: 2)")
    ap.add_argument("--db", help="Path to app.sqlite3 (overrides everything)")
    ap.add_argument("--data-dir", help="Pixarr data dir (uses <data-dir>/db/app.sqlite3)")
    args = ap.parse_args()

    db_path = resolve_db_path(args.db, args.data_dir)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        print("Tip: run an ingest, or point --data-dir/--db at the correct location.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM ingests ORDER BY started_at DESC LIMIT ?",
            (args.limit,),
        )
        rows = cur.fetchall()
        print_rows(rows)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
