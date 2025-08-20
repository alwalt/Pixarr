#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/make_test_zoo.sh [--data-dir /path/to/data]
#
# This version:
#   - EMBEDS EXIF timestamps in JPEG at save-time via Pillow
#   - EMBEDS MP4 creation_time using ffmpeg provided by the Python
#     package `imageio-ffmpeg` (no brew/system ffmpeg required)
#
# Requires:
#   pip install Pillow imageio-ffmpeg
#
# Notes:
#   - We do NOT require exiftool. If present, we may add extra tags, but it's optional.

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
have_pillow_py() { python3 - <<'PY' >/dev/null 2>&1
import sys
try:
    import PIL  # type: ignore
    from PIL import Image  # ensure module works
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

get_py_ffmpeg_path() { python3 - <<'PY'
import sys
try:
    import imageio_ffmpeg  # ships a prebuilt ffmpeg binary
    print(imageio_ffmpeg.get_ffmpeg_exe())
except Exception as e:
    sys.stderr.write("ERROR: imageio-ffmpeg not available in this Python env.\n")
    sys.stderr.write("       pip install imageio-ffmpeg\n")
    sys.exit(1)
PY
}

need() { command -v "$1" >/dev/null 2>&1; }

# --- hard requirements for “legit” assets ---
if ! have_pillow_py; then
  echo "ERROR: Pillow is required to write EXIF timestamps for JPEG." >&2
  echo "       Try:  pip install Pillow" >&2
  exit 1
fi
FFMPEG_BIN="$(get_py_ffmpeg_path)"  # <- from Python package imageio-ffmpeg
echo "Using Python-managed ffmpeg at: $FFMPEG_BIN"

# -----------------------------------------------
# 1) Legit JPEG with EXIF DateTimeOriginal/CreateDate (via Pillow)
# -----------------------------------------------
JPEG_OK="$TESTDIR/exif_ok_2024-01-16_15-57-40.jpg"
python3 - <<'PY' "$JPEG_OK"
from PIL import Image
import sys
out = sys.argv[1]
ts  = "2024:01:16 15:57:40"   # EXIF timestamp format

# Make a tiny red image
im = Image.new("RGB", (4, 4), (255, 0, 0))

# Write EXIF at save time (Pillow >=9.1 has Image.Exif)
try:
    exif = Image.Exif()
    exif[36867] = ts  # DateTimeOriginal
    exif[36868] = ts  # CreateDate (Digitized)
    im.save(out, "JPEG", quality=85, exif=exif)
except Exception:
    # Fallback for older Pillow: save without EXIF (still valid image)
    # You can later backfill with exiftool if you want, but it's optional.
    im.save(out, "JPEG", quality=85)
PY

# -------------------------------------------------
# 1b) JPEG metadata-only variant (same pixels, diff bytes)
#     -> Should keep SAME content_sha256 (decoded pixels)
# ---------------------------------------------------
JPEG_META="$TESTDIR/exif_ok_metaonly.jpg"
cp -p "$JPEG_OK" "$JPEG_META"
# (Optionally add metadata if exiftool is present; not required)
if need exiftool; then
  exiftool -overwrite_original \
    -XPComment="meta tweak without pixel change" \
    -Subject+="unit-test" \
    "$JPEG_META" >/dev/null || true
fi

# -------------------------------------------------
# 2) Legit MP4 with creation_time using Python-managed ffmpeg
# -------------------------------------------------
MP4_OK="$TESTDIR/vid_ok_2024-07-10_20-08-42.mp4"
"$FFMPEG_BIN" -loglevel error \
  -f lavfi -i color=c=blue:s=16x16:d=1 \
  -f lavfi -i anullsrc=r=48000:cl=mono -shortest \
  -metadata creation_time="2024-07-10T20:08:42Z" \
  -metadata:s:v:0 creation_time="2024-07-10T20:08:42Z" \
  -metadata:s:a:0 creation_time="2024-07-10T20:08:42Z" \
  -movflags +faststart -pix_fmt yuv420p -y "$MP4_OK"

# If exiftool exists, optionally stamp QuickTime keys too (nice-to-have)
if need exiftool; then
  exiftool -overwrite_original \
    -QuickTime:CreateDate="2024:07:10 20:08:42+00:00" \
    -QuickTime:ModifyDate="2024:07:10 20:08:42+00:00" \
    -MediaCreateDate="2024:07:10 20:08:42+00:00" \
    -TrackCreateDate="2024:07:10 20:08:42+00:00" \
    -Keys:CreationDate="2024-07-10T20:08:42Z" \
    "$MP4_OK" >/dev/null || true
fi

# -------------------------------------------------------------------
# 3) PNG & GIF (PNG will usually be missing_datetime, by design)
# -------------------------------------------------------------------
python3 - <<'PY' "$TESTDIR"
from pathlib import Path
from PIL import Image, PngImagePlugin
import sys

td = Path(sys.argv[1])

# PNG base: has alpha so our content hash flattens predictably
im = Image.new("RGBA",(4,4),(0,255,0,128))
im.save(td/"screenshot1.png","PNG")

# Animated GIF (2 frames)
im = Image.new("P",(4,4))
im2 = Image.new("P",(4,4))
im.save(td/"anim.gif","GIF", save_all=True, append_images=[im2])
PY

# -----------------------------------------------------------------------------------
# 3b) PNG metadata-only variant (same pixels, different bytes -> same content hash)
# -----------------------------------------------------------------------------------
python3 - <<'PY' "$TESTDIR"
from PIL import Image, PngImagePlugin
from pathlib import Path
td = Path(__import__('sys').argv[1])
base = td/"content_base.png"
meta = td/"content_metaonly.png"

im = Image.new("RGBA",(4,4),(123,45,67,255))
im.save(base, "PNG")

meta_info = PngImagePlugin.PngInfo()
meta_info.add_text("Comment", "metadata only")
im.save(meta, "PNG", pnginfo=meta_info)
PY

# ----------------------------------------------------------
# 4) Exact duplicate of the valid JPEG (same file hash)
# ----------------------------------------------------------
cp -p "$JPEG_OK" "$TESTDIR/dupe_of_exif_ok.jpg"

# ---------------------------------------------------------
# 5) Filename-date fallback JPEG (no EXIF set intentionally)
# ---------------------------------------------------------
FN_DATE="$TESTDIR/PHOTO-2023-12-01-09-10-11.jpg"
python3 - <<'PY' "$FN_DATE"
from PIL import Image
import sys
Image.new("RGB",(4,4),(0,0,255)).save(sys.argv[1],"JPEG", quality=85)
PY

# ------------------------------
# 6) Zero-byte supported ext
# ------------------------------
: > "$TESTDIR/empty_zero.heic"

# ------------------------------
# 7) Unsupported extension
# ------------------------------
echo "hello" > "$TESTDIR/notes.pdf"

# ------------------------------
# 8) Junk files
# ------------------------------
: > "$TESTDIR/.DS_Store"
: > "$TESTDIR/._junk.bin"
echo "thumbs" > "$TESTDIR/Thumbs.db"

# ---------------------------------------
# 9) Broken symlink (stat_error path)
# ---------------------------------------
ln -sf "/nope/missing" "$TESTDIR/broken_symlink.mov" || true

# ------------------------------------------------------------------
# 10) Sentinel/epoch date video (ignored -> missing_datetime)
# ------------------------------------------------------------------
MP4_EPOCH="$TESTDIR/epoch_1970.mov"
"$FFMPEG_BIN" -loglevel error -f lavfi -i color=c=black:s=16x16:d=1 -y "$MP4_EPOCH"
if need exiftool; then
  exiftool -overwrite_original -QuickTime:CreateDate="1970:01:01 00:00:00+00:00" "$MP4_EPOCH" >/dev/null || true
fi

echo "Done."
echo
if need exiftool; then
  echo "Verify timestamps (optional):"
  echo "  exiftool -time:all -a -G1 -s '$JPEG_OK'"
  echo "  exiftool -time:all -a -G1 -s '$MP4_OK'"
else
  echo "(Install exiftool if you want to inspect time metadata easily.)"
fi
echo
echo "Run ingest:"
echo "  python scripts/ingest_pass.py 'Staging/other/_testcase_zoo' --write -v --data-dir '$DATA_DIR'"
