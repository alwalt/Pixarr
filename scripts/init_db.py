from pathlib import Path
import sqlite3

DB_PATH = Path("data/db/app.sqlite3")
SCHEMA_PATH = Path("db/schema.sql")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

if not DB_PATH.exists():
    print("Creating new database...")
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.close()
    print("Database initialized at", DB_PATH)
else:
    print("Database already exists at", DB_PATH)
