# app/services/metadata.py
from __future__ import annotations
from pathlib import Path
from functools import lru_cache
import json, subprocess, shutil

# Optional Pillow fallback for images
try:
    from PIL import Image, ExifTags
except Exception:
    Image = None
    ExifTags = None

def _has_exiftool() -> bool:
    return shutil.which("exiftool") is not None

def _via_exiftool(p: Path) -> dict:
    """
    Return raw exiftool tags as a flat dict.
    We exclude known huge/binary blobs at the CLI level.
    """
    cmd = [
        "exiftool",
        "-j", "-n", "-G1",
        "-api", "largefilesupport=1",
        "--MakerNotes", "--PreviewImage", "--ThumbnailImage",
        str(p),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"exiftool rc={proc.returncode}")
    data = json.loads(proc.stdout) or [{}]
    row = dict(data[0])
    row.pop("SourceFile", None)
    return {str(k): row[k] for k in row}

def _via_pillow(p: Path) -> dict:
    """Very small, image-only fallback; still generic (whatever Pillow exposes)."""
    if Image is None:
        return {}
    out: dict = {}
    try:
        with Image.open(p) as im:
            out["Basic:Format"] = im.format
            w, h = getattr(im, "size", (None, None))
            if w is not None: out["Basic:Width"] = int(w)
            if h is not None: out["Basic:Height"] = int(h)
            exif = getattr(im, "getexif", None)
            if exif:
                raw = exif()
                if raw:
                    tagmap = getattr(ExifTags, "TAGS", {})
                    for tag_id, val in raw.items():
                        name = tagmap.get(tag_id, f"EXIF:{tag_id}")
                        out[str(name)] = _to_jsonable(val)
    except Exception:
        pass
    return out

def _to_jsonable(v):
    """Generic: make any value JSON-serializable without special casing fields."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    try:
        json.dumps(v)
        return v
    except Exception:
        # Last resort: string form
        return str(v)

# Generic compact rules (NOT per-field: pattern/namespace level).
_EXCLUDE_PREFIXES = (
    # If exiftool args change, you can still drop noisy namespaces here.
    "MakerNotes:",    # vendor blobs
    "ICC_Profile:",   # color profile dumps
)
_EXCLUDE_EXACT = {
    # In case plugins add these back
    "Composite:PreviewImage",
    "PreviewImage",
    "ThumbnailImage",
}

def _compact(meta: dict) -> dict:
    """Generic compaction: drop noisy/binary-ish keys; stringify complex types."""
    out: dict = {}
    for k, v in meta.items():
        if any(k.startswith(pref) for pref in _EXCLUDE_PREFIXES):
            continue
        if k in _EXCLUDE_EXACT:
            continue
        out[str(k)] = _to_jsonable(v)
    return out

@lru_cache(maxsize=256)
def _cached_read(path_str: str, mtime_ns: int, size: int, compact: bool) -> dict:
    p = Path(path_str)
    try:
        meta = _via_exiftool(p) if _has_exiftool() else _via_pillow(p)
        meta["_source"] = "exiftool" if _has_exiftool() else "pillow"
    except Exception as e:
        # If exiftool fails, try pillow and keep the error note
        meta = {"_error": str(e), **_via_pillow(p)}
        meta["_source"] = meta.get("_source", "pillow")
    meta = _compact(meta) if compact else {k: _to_jsonable(v) for k, v in meta.items()}
    return meta

def read_metadata(p: Path, *, compact: bool = True) -> dict:
    """
    Public API: return all available tags (optionally compacted), plus basic file stats.
    No per-field mappers—whatever exists is returned.
    """
    st = p.stat()
    meta = _cached_read(
        str(p),
        getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
        st.st_size,
        compact,
    )
    # Add a few generic basics; these are not "EXIF fields", just file info.
    meta.setdefault("Basic:Filename", p.name)
    meta.setdefault("Basic:Size", st.st_size)
    meta.setdefault("Basic:Modified", int(st.st_mtime))
    return meta
