# app/schemas/media.py
from pydantic import BaseModel
from typing import Optional, List

class MediaItem(BaseModel):
    id: str
    canonical_path: str
    taken_at: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    media_url: str
    thumb_url: Optional[str] = None

class StagingEntry(BaseModel):
    name: str
    rel_path: str
    is_dir: bool
    size: Optional[int] = None
    mtime: Optional[str] = None
    media_url: Optional[str] = None
    thumb_url: Optional[str] = None

class StagingStats(BaseModel):
    images: int
    videos: int
    raw: int
    other: int
    dirs: int
    total_files: int