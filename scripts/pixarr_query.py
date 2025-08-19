#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pixarr — DB inspection CLI

Examples:
  # Quarantined files that were NOT moved (dry-run or move failed), newest first
  python scripts/pixarr_query.py --data-dir /Volumes/Data/Pixarr/data quarantine --unmoved-only

  # Same, filtered by reason and limited
  python scripts/pixarr_query.py quarantine --reason missing_datetime --unmoved-only --limit 50

  # All sightings that match a filename pattern
  python scripts/pixarr_query.py sightings --like "IMG_079%" --limit 100

  # Sightings from a specific ingest/batch
  python scripts/pixarr_query.py sightings --ingest-id 2a121df8-834e-4dce-8a90-4bd407a7d43b

  # Quarantine reason counts
  python scripts/pixarr_query.py reasons

  # State counts
  python scripts/pixarr_query.py states
"""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# --- path helpers (match ingest script defaults) ---
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def db_path_for(data_dir: Path) -> Path:
    return data_dir / "db" / "app.sqlite3"

# --- connect ---
def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# --- pretty printing ---
def print_rows(rows, columns=None, limit=None):
    n = 0
    for r in rows:
        if columns:
            line = " | ".join(f"{k}={r[k]}" for k in columns if k in r.keys())
        else:
            line = " | ".join(f"{k}={r[k]}" for k in r.keys())
        print(line)
        n += 1
        if limit and n >= limit:
            break
    if n == 0:
        print("(no rows)")

# --- subcommands ---
def cmd_quarantine(args):
    conn = connect(db_path_for(Path(args.data_dir)))
    where = ["m.state = 'quarantine'"]
    params = []

    if args.unmoved_only:
        where.append("(m.canonical_path IS NULL OR TRIM(m.canonical_path) = '')")

    if args.reason:
        where.append("m.quarantine_reason = ?")
        params.append(args.reason)

    if args.hours is not None:
        since = (datetime.utcnow() - timedelta(hours=args.hours)).isoformat()
        where.append("s.seen_at >= ?")
        params.append(since)
    elif args.since:
        where.append("s.seen_at >= ?")
        params.append(args.since)

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT
          m.id,
          m.quarantine_reason,
          m.ext, m.bytes,
          m.canonical_path,
          s.filename,
          s.full_path,
          s.seen_at,
          s.ingest_id
        FROM media m
        JOIN sightings s ON s.media_id = m.id
        {where_sql}
        ORDER BY s.seen_at DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    print_rows(rows, columns=[
        "seen_at","quarantine_reason","filename","full_path","canonical_path","id","ingest_id"
    ])
    conn.close()

def cmd_sightings(args):
    conn = connect(db_path_for(Path(args.data_dir)))
    where = []
    params = []

    if args.media_id:
        where.append("s.media_id = ?")
        params.append(args.media_id)

    if args.like:
        where.append("s.filename LIKE ?")
        params.append(args.like)

    if args.ingest_id:
        where.append("s.ingest_id = ?")
        params.append(args.ingest_id)

    if args.hours is not None:
        since = (datetime.utcnow() - timedelta(hours=args.hours)).isoformat()
        where.append("s.seen_at >= ?")
        params.append(since)
    elif args.since:
        where.append("s.seen_at >= ?")
        params.append(args.since)

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT
          s.seen_at, s.filename, s.full_path, s.ingest_id,
          m.id AS media_id, m.state, m.quarantine_reason, m.canonical_path
        FROM sightings s
        JOIN media m ON m.id = s.media_id
        {where_sql}
        ORDER BY s.seen_at DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    print_rows(rows, columns=[
        "seen_at","filename","full_path","state","quarantine_reason","canonical_path","media_id","ingest_id"
    ])
    conn.close()

def cmd_reasons(args):
    conn = connect(db_path_for(Path(args.data_dir)))
    sql = """
      SELECT quarantine_reason, COUNT(*) AS cnt
      FROM media
      WHERE state='quarantine'
      GROUP BY quarantine_reason
      ORDER BY cnt DESC;
    """
    rows = conn.execute(sql).fetchall()
    print_rows(rows)
    conn.close()

def cmd_batches(args):
    conn = connect(db_path_for(Path(args.data_dir)))
    sql = """
      SELECT id, source, started_at, finished_at, IFNULL(notes,'') AS notes
      FROM ingests
      ORDER BY started_at DESC
      LIMIT ?;
    """
    rows = conn.execute(sql, (args.limit,)).fetchall()
    print_rows(rows)
    conn.close()

def cmd_states(args):
    conn = connect(db_path_for(Path(args.data_dir)))
    base = "FROM media"
    where_sql = f" WHERE {args.where}" if args.where else ""
    rows = conn.execute(
        f"SELECT state, COUNT(*) AS n {base}{where_sql} "
        "GROUP BY state ORDER BY n DESC"
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) {base}{where_sql}").fetchone()[0]

    print("State counts:")
    for r in rows:
        print(f"  {r['state']:12s} {r['n']}")
    print(f"TOTAL: {total}")
    conn.close()

# --- main ---
def main():
    parser = argparse.ArgumentParser(description="Pixarr DB inspection")
    parser.add_argument("--data-dir", default=str(repo_root() / "data"),
                        help="Root data directory (default: ./data under repo)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_q = sub.add_parser("quarantine", help="List quarantined files (optionally unmoved-only)")
    p_q.add_argument("--reason", help="Filter by quarantine_reason (e.g., missing_datetime)")
    p_q.add_argument("--unmoved-only", action="store_true", help="Only rows with empty canonical_path")
    p_q.add_argument("--since", help="ISO8601 lower bound on sightings.seen_at")
    p_q.add_argument("--hours", type=int, help="Only last N hours")
    p_q.add_argument("--limit", type=int, default=200)
    p_q.set_defaults(func=cmd_quarantine)

    p_s = sub.add_parser("sightings", help="List sightings joined with media")
    p_s.add_argument("--media-id", help="Exact media.id")
    p_s.add_argument("--ingest-id", help="Filter by ingest batch UUID")
    p_s.add_argument("--like", help="Filename LIKE pattern, e.g. 'IMG_07%%'")
    p_s.add_argument("--since", help="ISO8601 lower bound on sightings.seen_at")
    p_s.add_argument("--hours", type=int, help="Only last N hours")
    p_s.add_argument("--limit", type=int, default=200)
    p_s.set_defaults(func=cmd_sightings)

    p_r = sub.add_parser("reasons", help="Quarantine reason counts")
    p_r.set_defaults(func=cmd_reasons)

    p_b = sub.add_parser("batches", help="List recent ingest batches")
    p_b.add_argument("--limit", type=int, default=20)
    p_b.set_defaults(func=cmd_batches)

    p_states = sub.add_parser("states", help="Count rows by media state")
    p_states.add_argument("--where", default=None,
                          help="Optional SQL WHERE on media, e.g. \"taken_at >= '2024-01-01'\"")
    p_states.set_defaults(func=cmd_states)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
