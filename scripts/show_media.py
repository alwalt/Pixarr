#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# scripts/show_media.py
#
# Usage examples:
#   python scripts/show_media.py --id bb6d33dc-91f3-5dd1-bfc9-d5e2fc1024bd
#   python scripts/show_media.py --hash 7151bea9f6a5...
#   python scripts/show_media.py --hash-prefix 242053ae
#   python scripts/show_media.py --path "/Volumes/Data/Pixarr/data/media/Staging/other/_testcase_zoo/dupe_of_exif_ok.jpg"
#   PIXARR_DATA_DIR=/Volumes/Data/Pixarr/data python scripts/show_media.py --hash-prefix 242053ae

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, List, Tuple

# --- optional TOML support (stdlib on 3.11+, else tomli if installed) ---
try:
    import tomllib as _toml  # Python 3.11+
except Exception:            # pragma: no cover
    try:
        import tomli as _toml  # Python ≤3.10
    except Exception:
        _toml = None


def repo_root() -> Path:
    """Assumes this file lives in repo_root/scripts/"""
    return Path(__file__).resolve().parents[1]


def find_default_db() -> Tuple[Optional[Path], List[Path]]:
    """Discover app.sqlite3 from env/config; return (found_path_or_None, tried_paths)."""
    tried: List[Path] = []

    # 1) Explicit env override
    if (env_db := os.environ.get("PIXARR_DB")):
        p = Path(env_db).expanduser()
        tried.append(p)
        return (p if p.exists() else None, tried)

    # 2) DATA_DIR env
    if (env_dd := os.environ.get("PIXARR_DATA_DIR")):
        p = Path(env_dd).expanduser() / "db" / "app.sqlite3"
        tried.append(p)
        return (p if p.exists() else None, tried)

    # 3) pixarr.toml [paths].data_dir from repo root or CWD
    if _toml:
        for root in (repo_root(), Path.cwd()):
            cfg = root / "pixarr.toml"
            if cfg.exists():
                try:
                    data = _toml.loads(cfg.read_text(encoding="utf-8"))
                    dd = data.get("paths", {}).get("data_dir")
                    if dd:
                        p = Path(dd).expanduser() / "db" / "app.sqlite3"
                        tried.append(p)
                        return (p if p.exists() else None, tried)
                except Exception:
                    pass

    # 4) repo default
    p1 = repo_root() / "data" / "db" / "app.sqlite3"
    tried.append(p1)
    if p1.exists():
        return (p1, tried)

    # 5) CWD fallback
    p2 = Path.cwd() / "data" / "db" / "app.sqlite3"
    tried.append(p2)
    return (p2 if p2.exists() else None, tried)


def print_table(title: str, rows: Iterable[sqlite3.Row]) -> None:
    """Render a simple ASCII table from sqlite rows."""
    rows = list(rows)
    print(f"\n== {title} ==")
    if not rows:
        print("(no rows)")
        return
    headers = rows[0].keys()
    widths = [len(h) for h in headers]
    for r in rows:
        for i, h in enumerate(headers):
            v = r[h]
            widths[i] = max(widths[i], len("" if v is None else str(v)))

    def line(fill: str = "-") -> None:
        print("+" + "+".join(fill * (w + 2) for w in widths) + "+")

    line()
    print("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    line("=")
    for r in rows:
        print("| " + " | ".join(
            ("" if r[h] is None else str(r[h])).ljust(widths[i])
            for i, h in enumerate(headers)
        ) + " |")
    line()


def resolve_media_id(conn: sqlite3.Connection, args) -> str:
    """Turn --id/--hash/--hash-prefix/--path into a concrete media.id"""
    if args.id:
        return args.id

    if args.hash:
        row = conn.execute(
            "SELECT id FROM media WHERE hash_sha256 = ? LIMIT 1",
            (args.hash,)
        ).fetchone()
        if not row:
            raise SystemExit("No media found for that --hash.")
        return row["id"]

    if args.hash_prefix:
        rows = conn.execute(
            """
            SELECT id, hash_sha256, taken_at, state, canonical_path, updated_at
            FROM media
            WHERE substr(hash_sha256, 1, ?) = ?
            ORDER BY updated_at DESC
            """,
            (len(args.hash_prefix), args.hash_prefix),
        ).fetchall()
        if not rows:
            raise SystemExit("No media found for that --hash-prefix.")
        if len(rows) > 1:
            print_table("multiple matches (use a longer --hash-prefix)", rows)
            raise SystemExit(2)
        return rows[0]["id"]

    if args.path:
        row = conn.execute(
            """
            SELECT media_id
            FROM sightings
            WHERE full_path = ?
            ORDER BY seen_at DESC
            LIMIT 1
            """,
            (args.path,),
        ).fetchone()
        if not row:
            raise SystemExit("No sightings found for that --path.")
        return row["media_id"]

    raise SystemExit("No selector provided.")


def main() -> None:
    default_db, tried = find_default_db()

    ap = argparse.ArgumentParser(description="Show one media row and its related records.")
    ap.add_argument(
        "--db",
        default=str(default_db) if default_db else None,
        help="Path to app.sqlite3 (auto-detected from env/config if omitted)"
    )

    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="media.id (UUID)")
    g.add_argument("--hash", help="full SHA-256 of the file")
    g.add_argument("--hash-prefix", help="first N chars of SHA-256 (e.g., 242053ae)")
    g.add_argument("--path", help="absolute file path as recorded in sightings.full_path")

    args = ap.parse_args()

    db_path = Path(args.db).expanduser() if args.db else None
    if not db_path or not db_path.exists():
        print("ERROR: Could not locate the database file.")
        print("Tried:")
        for p in tried:
            print(f"  - {p}")
        print("\nTips:")
        print("  * set PIXARR_DB=/path/to/app.sqlite3")
        print("  * or set PIXARR_DATA_DIR=/path/to/data (expects db/app.sqlite3 inside)")
        print("  * or define [paths].data_dir in pixarr.toml at repo root")
        raise SystemExit(2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    media_id = resolve_media_id(conn, args)

    # --- media row
    print_table("media", conn.execute(
        """
        SELECT id, hash_sha256, ext, bytes, taken_at, state,
               canonical_path, quarantine_reason, added_at, updated_at, xmp_written
        FROM media
        WHERE id = ?
        """,
        (media_id,),
    ))

    # --- sightings
    print_table("sightings", conn.execute(
        """
        SELECT source_root, filename, full_path, folder_hint, ingest_id, seen_at
        FROM sightings
        WHERE media_id = ?
        ORDER BY seen_at DESC
        """,
        (media_id,),
    ))

    # --- optional related tables
    print_table("album_hints", conn.execute(
        "SELECT * FROM album_hints WHERE media_id = ? ORDER BY created_at",
        (media_id,),
    ))
    print_table("media_tags", conn.execute(
        "SELECT * FROM media_tags WHERE media_id = ? ORDER BY namespace, tag",
        (media_id,),
    ))
    print_table("exif_kv", conn.execute(
        "SELECT * FROM exif_kv WHERE media_id = ? ORDER BY tag",
        (media_id,),
    ))

    # --- quick summary: sightings by source
    print_table("sightings_count_by_source", conn.execute(
        """
        SELECT source_root, COUNT(*) AS sightings
        FROM sightings
        WHERE media_id = ?
        GROUP BY source_root
        ORDER BY sightings DESC, source_root
        """,
        (media_id,),
    ))

    conn.close()


if __name__ == "__main__":
    main()
