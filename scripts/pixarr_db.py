#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pixarr DB tools — lightweight CLI for querying app.sqlite3

Examples:
  # point at default DB (repo/data/db/app.sqlite3)
  ./scripts/pixarr_db.py states
  ./scripts/pixarr_db.py reasons
  ./scripts/pixarr_db.py quarantined --limit 20

  # unique values / value counts
  ./scripts/pixarr_db.py unique media ext
  ./scripts/pixarr_db.py value-counts media quarantine_reason --where "state='quarantine'"

  # batches
  ./scripts/pixarr_db.py batches --limit 10
  ./scripts/pixarr_db.py batch-items --id 3e7bcd1c-... --limit 25

  # review queue sample
  ./scripts/pixarr_db.py review --limit 20

  # ad-hoc SQL (or from file with @path.sql)
  ./scripts/pixarr_db.py run --sql "SELECT state, COUNT(*) FROM media GROUP BY state"

  chmod +x scripts/pixarr_db.py

    # State + quarantine summaries
    scripts/pixarr_db.py states
    scripts/pixarr_db.py reasons

    # Explore unique values / value counts (generic)
    scripts/pixarr_db.py unique media ext
    scripts/pixarr_db.py value-counts media quarantine_reason --where "state='quarantine'"

    # Recent quarantined and review queue
    scripts/pixarr_db.py quarantined --limit 25
    scripts/pixarr_db.py review --limit 25

    # Batches
    scripts/pixarr_db.py batches
    scripts/pixarr_db.py batch-items --id <ingest-uuid>

    # Anything else:
    scripts/pixarr_db.py run --sql "SELECT * FROM media LIMIT 5"

"""

import argparse
import os
import sqlite3
from pathlib import Path
from typing import List, Sequence, Tuple, Optional

def repo_root() -> Path:
    # same approach as ingest script: scripts/ is one level under repo root
    return Path(__file__).resolve().parents[1]

def default_db_path() -> Path:
    p = repo_root() / "data" / "db" / "app.sqlite3"
    return p

# ------- tiny table printer (stdlib only) -------

def _stringify(x):
    if x is None:
        return ""
    return str(x)

def print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    cols = len(headers)
    widths = [len(h) for h in headers]
    srows = []
    for row in rows:
        srow = [_stringify(v) for v in row]
        srows.append(srow)
        for i in range(cols):
            widths[i] = max(widths[i], len(srow[i]) if i < len(srow) else 0)

    def fmt_row(vals):
        parts = []
        for i, v in enumerate(vals):
            parts.append(v.ljust(widths[i]))
        return "  " + " | ".join(parts)

    # header
    if headers:
        print(fmt_row(headers))
        print("  " + "-+-".join("-" * w for w in widths))

    for r in srows:
        print(fmt_row(r))

# ------- db helpers -------

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def fetch_all(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> Tuple[List[str], List[Tuple]]:
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    headers = [d[0] for d in cur.description] if cur.description else []
    return headers, [tuple(r) for r in rows]

# ------- commands -------

def cmd_states(conn, args):
    sql = "SELECT state, COUNT(*) AS cnt FROM media GROUP BY state ORDER BY cnt DESC"
    headers, rows = fetch_all(conn, sql)
    print_table(headers, rows)

def cmd_reasons(conn, args):
    sql = """
      SELECT quarantine_reason, COUNT(*) AS cnt
      FROM media
      WHERE state='quarantine'
      GROUP BY quarantine_reason
      ORDER BY cnt DESC
    """
    headers, rows = fetch_all(conn, sql)
    print_table(headers, rows)

def cmd_quarantined(conn, args):
    sql = """
      SELECT id, ext, bytes, quarantine_reason, updated_at, canonical_path
      FROM media
      WHERE state='quarantine'
      ORDER BY updated_at DESC
      LIMIT ?
    """
    headers, rows = fetch_all(conn, sql, (args.limit,))
    print_table(headers, rows)

def cmd_review(conn, args):
    # Prefer the view if it exists
    view_exists = fetch_all(conn,
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_review_queue'")[1]
    if view_exists:
        sql = "SELECT id, canonical_path, taken_at FROM v_review_queue LIMIT ?"
        headers, rows = fetch_all(conn, sql, (args.limit,))
    else:
        sql = """
          SELECT id, canonical_path, taken_at
          FROM media
          WHERE state='review'
          ORDER BY (taken_at IS NULL), taken_at
          LIMIT ?
        """
        headers, rows = fetch_all(conn, sql, (args.limit,))
    print_table(headers, rows)

def cmd_batches(conn, args):
    sql = """
      SELECT id, source, started_at, finished_at, notes
      FROM ingests
      ORDER BY IFNULL(finished_at, started_at) DESC
      LIMIT ?
    """
    headers, rows = fetch_all(conn, sql, (args.limit,))
    print_table(headers, rows)

def cmd_batch_items(conn, args):
    if not args.id:
        raise SystemExit("--id <ingest-uuid> is required")
    sql = """
      SELECT s.filename, s.source_root, s.full_path, s.seen_at,
             m.state, m.quarantine_reason, m.taken_at, m.canonical_path
      FROM sightings s
      JOIN media m ON m.id = s.media_id
      WHERE s.ingest_id = ?
      ORDER BY s.seen_at DESC
      LIMIT ?
    """
    headers, rows = fetch_all(conn, sql, (args.id, args.limit))
    print_table(headers, rows)

def cmd_unique(conn, args):
    table = args.table
    col = args.column
    where = f" WHERE {args.where} " if args.where else ""
    sql = f"SELECT DISTINCT {col} FROM {table}{where} ORDER BY {col} LIMIT ?"
    headers, rows = fetch_all(conn, sql, (args.limit,))
    print_table(headers, rows)

def cmd_value_counts(conn, args):
    table = args.table
    col = args.column
    where = f" WHERE {args.where} " if args.where else ""
    sql = f"""
      SELECT {col} AS value, COUNT(*) AS cnt
      FROM {table}
      {where}
      GROUP BY {col}
      ORDER BY cnt DESC
      LIMIT ?
    """
    headers, rows = fetch_all(conn, sql, (args.limit,))
    print_table(headers, rows)

def cmd_run(conn, args):
    sql = args.sql
    if not sql:
        raise SystemExit("Provide --sql '<query>' or --sql @file.sql")
    if sql.startswith("@"):
        sql_path = Path(sql[1:]).expanduser().resolve()
        sql = sql_path.read_text(encoding="utf-8")
    headers, rows = fetch_all(conn, sql)
    print_table(headers, rows)

def cmd_schema(conn, args):
    # tables
    th, trs = fetch_all(conn, "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type,name")
    print_table(th, trs)
    print()
    # columns (for a specific table)
    if args.table:
        ch, crs = fetch_all(conn, f"PRAGMA table_info({args.table})")
        print_table(ch, crs)

def cmd_check(conn, args):
    checks = [
        ("Reason present outside quarantine (should be 0)",
         "SELECT COUNT(*) FROM media WHERE state!='quarantine' AND quarantine_reason IS NOT NULL"),
        ("Duplicate content hashes (>1 rows with same hash)",
         "SELECT COUNT(*) FROM (SELECT hash_sha256 FROM media GROUP BY hash_sha256 HAVING COUNT(*)>1)"),
    ]
    for title, sql in checks:
        h, r = fetch_all(conn, sql)
        val = r[0][0] if r else "0"
        print(f"- {title}: {val}")
    # optionally list duplicates
    if args.list_duplicates:
        print("\nDuplicate hashes:")
        h, r = fetch_all(conn, """
          SELECT hash_sha256, COUNT(*) AS cnt
          FROM media
          GROUP BY hash_sha256
          HAVING cnt>1
          ORDER BY cnt DESC, hash_sha256
          LIMIT 100
        """)
        print_table(h, r)

# ------- main -------

def main():
    ap = argparse.ArgumentParser(description="Pixarr DB query helper")
    ap.add_argument("--db", default=str(default_db_path()),
                    help="Path to app.sqlite3 (default: repo/data/db/app.sqlite3)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("states", help="Count rows per media.state").set_defaults(func=cmd_states)
    sub.add_parser("reasons", help="Count rows per quarantine_reason (only state=quarantine)").set_defaults(func=cmd_reasons)

    spq = sub.add_parser("quarantined", help="List recent quarantined rows")
    spq.add_argument("--limit", type=int, default=50)
    spq.set_defaults(func=cmd_quarantined)

    spr = sub.add_parser("review", help="Sample review queue")
    spr.add_argument("--limit", type=int, default=50)
    spr.set_defaults(func=cmd_review)

    spb = sub.add_parser("batches", help="List recent ingest batches")
    spb.add_argument("--limit", type=int, default=20)
    spb.set_defaults(func=cmd_batches)

    spbi = sub.add_parser("batch-items", help="Show items seen in a specific ingest batch")
    spbi.add_argument("--id", required=True, help="ingest UUID")
    spbi.add_argument("--limit", type=int, default=50)
    spbi.set_defaults(func=cmd_batch_items)

    spu = sub.add_parser("unique", help="Distinct values for a column")
    spu.add_argument("table")
    spu.add_argument("column")
    spu.add_argument("--where", help="Optional WHERE clause (without 'WHERE')")
    spu.add_argument("--limit", type=int, default=200)
    spu.set_defaults(func=cmd_unique)

    spvc = sub.add_parser("value-counts", help="Value counts for a column")
    spvc.add_argument("table")
    spvc.add_argument("column")
    spvc.add_argument("--where", help="Optional WHERE clause (without 'WHERE')")
    spvc.add_argument("--limit", type=int, default=200)
    spvc.set_defaults(func=cmd_value_counts)

    sprun = sub.add_parser("run", help="Run ad-hoc SQL or @file.sql")
    sprun.add_argument("--sql", required=True)
    sprun.set_defaults(func=cmd_run)

    sps = sub.add_parser("schema", help="List tables/views; show PRAGMA table_info for a table")
    sps.add_argument("--table", help="Optional table name to show columns")
    sps.set_defaults(func=cmd_schema)

    spc = sub.add_parser("check", help="Quick consistency checks (reasons, duplicates)")
    spc.add_argument("--list-duplicates", action="store_true")
    spc.set_defaults(func=cmd_check)

    args = ap.parse_args()
    conn = connect(Path(args.db).expanduser().resolve())
    try:
        args.func(conn, args)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
