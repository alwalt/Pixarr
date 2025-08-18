import builtins
from datetime import datetime

# Import your functions from the script
# If your file is scripts/ingest_pass.py, this works when running from repo root:
from scripts.ingest_pass import resolve_taken_at, _taken_from_filename, extract_taken_at_exif_only

def test_exif_wins_over_filename():
    meta = {"DateTimeOriginal": "2024:07:08 08:00:38"}
    # even if filename looks like another date, EXIF should win
    out = resolve_taken_at(meta, "PHOTO-2019-01-01-01-02-03.jpg", allow_filename_dates=True)
    assert out.startswith("2024-07-08T08:00:38")

def test_filename_fallback_enabled():
    meta = {}  # no EXIF
    out = resolve_taken_at(meta, "PHOTO-2024-07-10-20-08-42.jpg", allow_filename_dates=True)
    assert out.startswith("2024-07-10T20:08:42")

def test_filename_fallback_disabled_goes_none():
    meta = {}  # no EXIF
    out = resolve_taken_at(meta, "PHOTO-2024-07-10-20-08-42.jpg", allow_filename_dates=False)
    assert out is None

def test_filename_patterns():
    # sanity checks for the helper itself
    assert _taken_from_filename("IMG_20240710_200842.HEIC") == datetime(2024,7,10,20,8,42)
    assert _taken_from_filename("WhatsApp Image 2024-07-10 at 20.08.42.jpeg") == datetime(2024,7,10,20,8,42)
    assert _taken_from_filename("PXL_20240710_200842123.jpg") == datetime(2024,7,10,20,8,42)

