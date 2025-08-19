#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/make_test_zoo.sh [--data-dir /path/to/data]
#
# Default DATA_DIR resolves to repo_root/data

# --- resolve repo root ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DATA_DIR="$REPO_ROOT/data"
if [[ "${1:-}" == "--data-dir" && -n "${2:-}" ]]; then
  DATA_DIR="$2"
fi

TESTDIR="$DATA_DIR/media/Staging/other/_testcase_zoo"
mkdir -p "$TESTDIR"

echo "Writing test files to: $TESTDIR"

# --- helpers ---
need() { command -v "$1" >/dev/null 2>&1; }
have_pillow_py() { python3 - <<'PY' >/dev/null 2>&1
import sys
try:
    import PIL # type: ignore
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

# --- 1) Valid JPEG with EXIF DateTimeOriginal ---
JPEG_OK="$TESTDIR/exif_ok_2024-01-16_15-57-40.jpg"
if have_pillow_py; then
  python3 - <<PY "$JPEG_OK"
from PIL import Image
import sys
img = Image.new("RGB",(4,4),(255,0,0))
img.save(sys.argv[1], "JPEG", quality=85)
PY
  if need exiftool; then
    exiftool -overwrite_original \
      -DateTimeOriginal="2024:01:16 15:57:40" \
      -CreateDate="2024:01:16 15:57:40" \
      "$JPEG_OK" >/dev/null
  fi
else
  # Fallback: non-empty file (no EXIF); still useful for hashing/moves
  dd if=/dev/urandom of="$JPEG_OK" bs=1024 count=4 >/dev/null 2>&1
fi

# --- 2) Valid MP4 with QuickTime CreateDate ---
MP4_OK="$TESTDIR/vid_ok_2024-07-10_20-08-42.mp4"
if need ffmpeg; then
  ffmpeg -loglevel error -f lavfi -i color=c=blue:s=16x16:d=1 \
         -f lavfi -i anullsrc=r=48000:cl=mono -shortest \
         -movflags +faststart -pix_fmt yuv420p -y "$MP4_OK"
  if need exiftool; then
    exiftool -overwrite_original \
      -keys:creationdate="2024:07:10 20:08:42+00:00" \
      -QuickTime:CreateDate="2024:07:10 20:08:42+00:00" \
      "$MP4_OK" >/dev/null
  fi
else
  # Fallback: random bytes with .mp4 extension
  dd if=/dev/urandom of="$MP4_OK" bs=1024 count=10 >/dev/null 2>&1
fi

# --- 3) PNG & GIF (typically no EXIF → missing_datetime) ---
python3 - <<'PY' "$TESTDIR"
import sys
from pathlib import Path
try:
    from PIL import Image
    td = Path(sys.argv[1])
    im = Image.new("RGBA",(4,4),(0,255,0,255))
    im.save(td/"screenshot1.png","PNG")
    im = Image.new("P",(4,4))
    im.save(td/"anim.gif","GIF", save_all=True, append_images=[Image.new("P",(4,4))])
except Exception:
    import os
    open(os.path.join(sys.argv[1],"screenshot1.png"),"wb").write(b"\x89PNG\r\n\x1a\n")
    open(os.path.join(sys.argv[1],"anim.gif"),"wb").write(b"GIF89a")
PY

# --- 4) Duplicate of the valid JPEG (same SHA-256) ---
cp -p "$JPEG_OK" "$TESTDIR/dupe_of_exif_ok.jpg"

# --- 5) Filename-date fallback JPEG (no EXIF set intentionally) ---
FN_DATE="$TESTDIR/PHOTO-2023-12-01-09-10-11.jpg"
if have_pillow_py; then
  python3 - <<PY "$FN_DATE"
from PIL import Image
import sys
Image.new("RGB",(4,4),(0,0,255)).save(sys.argv[1],"JPEG", quality=85)
PY
else
  dd if=/dev/urandom of="$FN_DATE" bs=1024 count=2 >/dev/null 2>&1
fi

# --- 6) Zero-byte supported ext ---
: > "$TESTDIR/empty_zero.heic"

# --- 7) Unsupported extension ---
echo "hello" > "$TESTDIR/notes.pdf"

# --- 8) Junk files ---
: > "$TESTDIR/.DS_Store"
: > "$TESTDIR/._junk.bin"
echo "thumbs" > "$TESTDIR/Thumbs.db"

# --- 9) Broken symlink (stat_error path) ---
ln -sf "/nope/missing" "$TESTDIR/broken_symlink.mov"

# --- 10) Sentinel/epoch date video (ignored by resolver → missing_datetime) ---
MP4_EPOCH="$TESTDIR/epoch_1970.mov"
if need ffmpeg; then
  ffmpeg -loglevel error -f lavfi -i color=c=black:s=16x16:d=1 -y "$MP4_EPOCH"
  if need exiftool; then
    exiftool -overwrite_original -QuickTime:CreateDate="1970:01:01 00:00:00+00:00" "$MP4_EPOCH" >/dev/null
  fi
else
  dd if=/dev/urandom of="$MP4_EPOCH" bs=1024 count=8 >/dev/null 2>&1
fi

echo "Done."
echo "Try:"
echo "  python scripts/ingest_pass.py 'Staging/other/_testcase_zoo' -v --data-dir '$DATA_DIR'"
echo "  python scripts/ingest_pass.py 'Staging/other/_testcase_zoo' --allow-filename-dates -v --data-dir '$DATA_DIR'"
