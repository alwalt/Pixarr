#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("/Volumes/Data/Memories/.db/hub.db")

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM ingests ORDER BY started_at DESC LIMIT 2"
    )
    rows = cur.fetchall()
    if not rows:
        print("No ingests found.")
        return

    headers = rows[0].keys()
    print("\t".join(headers))
    print("-" * 80)

    for row in rows:
        print("\t".join(str(row[h]) if row[h] is not None else "" for h in headers))

    conn.close()

if __name__ == "__main__":
    main()
