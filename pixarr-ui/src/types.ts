// Shared types for API data

export type MediaItem = {
  id: string;
  canonical_path: string;
  taken_at?: string | null;
  gps_lat?: number | null;
  gps_lon?: number | null;
  media_url: string;           // absolute URL to original (from API)
  thumb_url?: string | null;   // absolute URL to thumbnail (optional)
};

export type MediaListResponse = MediaItem[];

export type StagingEntry = {
  name: string;
  rel_path: string;
  is_dir: boolean;
  size?: number | null;
  mtime?: string | null;
  media_url?: string | null;   // absolute URL to original (for files)
  thumb_url?: string | null;   // absolute URL to thumbnail (optional)
};

// Minimal shape used across features.
// Add fields as your API grows; keep this interface small.
export type PreviewItem = {
  id?: string;
  name: string;
  is_dir: boolean;
  media_url?: string;  // original file (video uses this)
  thumb_url?: string;  // server-rendered JPEG (works for HEIC)
  ext?: string;        // optional (e.g., ".heic", ".mp4")
  rel_path?: string;
};

export type ExifData = Record<string, string | number | null | undefined>;
