# app/utils/thumbs.py
from pathlib import Path
import hashlib
import io
from PIL import Image
from fastapi.responses import FileResponse, Response
from app.core.config import THUMB_DIR

def thumb_key(abs_path: Path, h: int) -> Path:
    """Cache key for a path+height; stored under THUMB_DIR as JPEG."""
    key = hashlib.sha1(f"{abs_path}|h={h}".encode()).hexdigest()
    return THUMB_DIR / f"{key}.jpg"

def make_thumb_bytes(abs_path: Path, h: int) -> bytes:
    """Load an image and return a resized JPEG as bytes with requested height."""
    with Image.open(abs_path) as im:
        im = im.convert("RGB")
        w, hh = im.size
        scale = (h / hh) if hh else 1.0
        new_w = max(int(w * scale), 1)
        im = im.resize((new_w, h))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue()

def serve_or_build_thumb(abs_path: Path, h: int):
    """
    Serve a cached thumbnail if present; otherwise build, cache, and serve it.
    On failure, fall back to serving the original file.
    """
    cache_path = thumb_key(abs_path, h)
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/jpeg")
    try:
        img_bytes = make_thumb_bytes(abs_path, h)
        cache_path.write_bytes(img_bytes)
        return Response(img_bytes, media_type="image/jpeg")
    except Exception:
        return FileResponse(abs_path)  # fallback if conversion fails
