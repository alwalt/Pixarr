#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
last_media.py — print the most recently added media rows from the Pixarr DB.

Usage (from repo root):
  python scripts/last_media.py                 # compact view (default)
  python scripts/last_media.py -n 10           # show 10 rows
  python scripts/last_media.py --full          # show all columns
  python scripts/last_media.py --tsv           # tab-separated output (compact cols)
  python scripts/last_media.py --data-dir /path/to/data
  python scripts/last_media.py --db /path/to/app.sqlite3

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
# ---------- tiny helpers (no exiftool imports) ----------

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

def human_bytes(n: Optional[int]) -> str:
    if n is None:
        return ""
    step = 1024.0
    units = ["B","KiB","MiB","GiB","TiB"]
    s = float(n)
    for u in units:
        if s < step or u == units[-1]:
            return f"{s:.0f}{u}" if u == "B" else f"{s:.1f}{u}"
        s /= step
    return f"{n}B"

def shorten(s: Optional[str], max_len: int = 40) -> str:
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    keep = max_len - 1
    head = keep // 2
    tail = keep - head
    return s[:head] + "…" + s[-tail:]

def row_compact(r: sqlite3.Row) -> dict:
    path = r["canonical_path"]
    return {
        "id8":     (r["id"] or "")[:8],
        "hash8":   (r["hash_sha256"] or "")[:8],
        "ext":     (r["ext"] or "").lstrip("."),
        "size":    human_bytes(r["bytes"]),
        "taken":   r["taken_at"] or "",
        "state":   r["state"] or "",
        "path":    shorten(path, 50),
    }

def print_full(rows, tsv: bool) -> None:
    if not rows:
        print("No rows found in media."); return
    headers = list(rows[0].keys())
    if tsv:
        print("\t".join(headers))
        for r in rows:
            print("\t".join("" if r[h] is None else str(r[h]) for h in headers))
        return
    # pretty fixed-width
    col_widths = [max(len(h), *(len("" if r[h] is None else str(r[h])) for r in rows)) for h in headers]
    sep = "  "
    header_line = sep.join(h.ljust(w) for h, w in zip(headers, col_widths))
    bar = "-" * len(header_line)
    print(header_line); print(bar)
    for r in rows:
        print(sep.join(("" if r[h] is None else str(r[h])).ljust(w) for h, w in zip(headers, col_widths)))

def print_compact(rows, tsv: bool) -> None:
    if not rows:
        print("No rows found in media."); return
    compact_rows = [row_compact(r) for r in rows]
    headers = ["id8","hash8","ext","size","taken","state","path"]
    if tsv:
        print("\t".join(headers))
        for cr in compact_rows:
            print("\t".join(cr[h] for h in headers))
        return
    # pretty
    col_widths = [max(len(h), *(len(cr[h]) for cr in compact_rows)) for h in headers]
    sep = "  "
    header_line = sep.join(h.ljust(w) for h, w in zip(headers, col_widths))
    bar = "-" * len(header_line)
    print(header_line); print(bar)
    for cr in compact_rows:
        print(sep.join(cr[h].ljust(w) for h, w in zip(headers, col_widths)))

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Show the most recent Pixarr media rows (compact by default).")
    ap.add_argument("-n", "--limit", type=int, default=2, help="How many rows to show (default: 2)")
    ap.add_argument("--db", help="Path to app.sqlite3 (overrides everything)")
    ap.add_argument("--data-dir", help="Pixarr data dir (uses <data-dir>/db/app.sqlite3)")
    ap.add_argument("--tsv", action="store_true", help="Tab-separated output (compact columns unless --full)")
    ap.add_argument("--full", action="store_true", help="Show all columns instead of compact view")
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
            # You can add WHERE state='review' here if you only care about review items
            "SELECT * FROM media ORDER BY added_at DESC LIMIT ?",
            (args.limit,),
        )
        rows = cur.fetchall()
        if args.full:
            print_full(rows, tsv=args.tsv)
        else:
            print_compact(rows, tsv=args.tsv)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
