# app/repositories/db.py
import sqlite3
from app.core.config import DB_PATH

def get_conn() -> sqlite3.Connection:
    """
    Create a connection with row access by column name.
    Caller is responsible for closing (use 'with get_conn() as con').
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
