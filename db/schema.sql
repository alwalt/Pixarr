-- =========================================================
-- Memories Hub — Canonical SQLite Schema
-- =========================================================

-- ----------
-- SQLite setup
-- ----------
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA case_sensitive_like=OFF;
PRAGMA user_version=1;

-- ----------
-- Core: one row per unique binary
-- ----------
CREATE TABLE IF NOT EXISTS media (
  id               TEXT PRIMARY KEY,              -- UUID (string)
  hash_sha256      TEXT UNIQUE NOT NULL,          -- dedup anchor
  phash            TEXT,                          -- optional perceptual hash
  ext              TEXT NOT NULL,                 -- .jpg .heic .mp4 ...
  bytes            INTEGER NOT NULL,
  taken_at         TEXT,                          -- ISO8601 (UTC or naive)
  tz_offset        TEXT,                          -- optional original TZ offset
  gps_lat          REAL,
  gps_lon          REAL,
  orientation      INTEGER,                       -- optional
  camera_make      TEXT,                          -- optional
  camera_model     TEXT,                          -- optional
  state            TEXT NOT NULL,                 -- 'staging'|'review'|'library'|'quarantine'|'deleted'
  canonical_path   TEXT,                          -- path in Review/Library
  added_at         TEXT NOT NULL,                 -- first-seen timestamp (UTC ISO8601)
  updated_at       TEXT NOT NULL,                 -- last update timestamp (UTC ISO8601)
  xmp_written      INTEGER DEFAULT 0,             -- 0/1 flag (written after library)
  -- deletion/verification (for reconcile scripts & audits)
  deleted_at       TEXT,                          -- when we marked it deleted
  last_verified_at TEXT,                          -- last time we saw the file on disk
  -- keep states constrained since we rebuild from file
  CHECK (state IN ('staging','review','library','quarantine','deleted'))
);
CREATE INDEX IF NOT EXISTS idx_media_taken_at ON media(taken_at);
CREATE INDEX IF NOT EXISTS idx_media_state    ON media(state);

-- ----------
-- Provenance: every path/name we've ever seen
-- ----------
CREATE TABLE IF NOT EXISTS sightings (
  id           INTEGER PRIMARY KEY,
  media_id     TEXT NOT NULL,
  source_root  TEXT NOT NULL,       -- e.g. 'Staging/pc', 'Staging/icloud'
  full_path    TEXT NOT NULL,       -- exact path when seen
  filename     TEXT NOT NULL,       -- basename at the time
  folder_hint  TEXT,                -- last human-looking folder (optional)
  ingest_id    TEXT,                -- batch id
  seen_at      TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media(id)   ON DELETE CASCADE,
  FOREIGN KEY(ingest_id) REFERENCES ingests(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_sightings_media   ON sightings(media_id);
CREATE INDEX IF NOT EXISTS idx_sightings_seen_at ON sightings(seen_at);
CREATE INDEX IF NOT EXISTS idx_sightings_ingest  ON sightings(ingest_id);

-- ----------
-- Hints: machine suggestions (not final tags)
-- ----------
CREATE TABLE IF NOT EXISTS album_hints (
  id           INTEGER PRIMARY KEY,
  media_id     TEXT NOT NULL,
  kind         TEXT,                -- 'folder'|'filename'|'exif'|'gps'
  value        TEXT,                -- 'Hawaii', 'Wedding', etc.
  confidence   REAL,                -- 0..1
  source_text  TEXT,
  created_at   TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hints_media ON album_hints(media_id);

-- ----------
-- Approved tags (what you'll write to XMP)
-- ----------
CREATE TABLE IF NOT EXISTS media_tags (
  media_id   TEXT NOT NULL,
  tag        TEXT NOT NULL,
  namespace  TEXT NOT NULL,         -- 'event'|'location'|'person'|'keyword'
  PRIMARY KEY(media_id, tag, namespace),
  FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON media_tags(tag);

-- ----------
-- Optional: albums (named sets) + membership
-- ----------
CREATE TABLE IF NOT EXISTS albums (
  id        TEXT PRIMARY KEY,       -- UUID
  title     TEXT NOT NULL,
  start_at  TEXT,
  end_at    TEXT
);
CREATE TABLE IF NOT EXISTS album_items (
  album_id  TEXT NOT NULL,
  media_id  TEXT NOT NULL,
  PRIMARY KEY(album_id, media_id),
  FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE,
  FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
);

-- ----------
-- Ingest batches (for auditing)
-- ----------
CREATE TABLE IF NOT EXISTS ingests (
  id          TEXT PRIMARY KEY,     -- UUID
  source      TEXT,                 -- 'icloudpd'|'rsync-pc'|'sdcard'|etc
  started_at  TEXT,
  finished_at TEXT,
  notes       TEXT
);

-- ----------
-- Flexible EXIF K/V for extras (optional)
-- ----------
CREATE TABLE IF NOT EXISTS exif_kv (
  media_id TEXT NOT NULL,
  tag      TEXT NOT NULL,
  value    TEXT,
  PRIMARY KEY(media_id, tag),
  FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_exif_media ON exif_kv(media_id);

-- ----------
-- Convenience views
-- ----------
DROP VIEW IF EXISTS v_review_queue;
CREATE VIEW v_review_queue AS
SELECT id, canonical_path, taken_at
FROM media
WHERE state='review'
ORDER BY (taken_at IS NULL), taken_at;  -- SQLite-friendly NULLs last

DROP VIEW IF EXISTS v_needs_xmp;
CREATE VIEW v_needs_xmp AS
SELECT id, canonical_path
FROM media
WHERE state='library' AND IFNULL(xmp_written,0)=0;

DROP VIEW IF EXISTS v_deleted;
CREATE VIEW v_deleted AS
SELECT id, canonical_path, taken_at, updated_at, deleted_at
FROM media
WHERE state='deleted';
