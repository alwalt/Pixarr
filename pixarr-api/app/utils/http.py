# app/utils/http.py
from pathlib import Path
from fastapi import Request

def safe_rel_under(base: Path, target: Path):
    """
    Return target's path relative to base if target is inside base, else None.
    Prevents path traversal.
    """
    try:
        return target.resolve().relative_to(base.resolve())
    except Exception:
        return None

def abs_url(request: Request, path: str) -> str:
    """Return absolute URL (scheme://host/path) for a given path."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"
