import { useEffect, useMemo, useState } from "react";
import type { StagingEntry } from "../types";
import Thumb from "./Thumb";

/* ----------------------------------------------------------------------------
   Types & helpers
---------------------------------------------------------------------------- */

// EXIF value shape (adjust as your API grows)
type ExifValue = string | number | boolean | null | undefined;
type ExifData = Record<string, ExifValue>;

// Safe error stringify so we can use `unknown` in catch blocks
function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// Theme passed from App for consistent dark styling
type Theme = {
  appBg: string;
  headerBg: string;
  surface: string;
  cardBg: string;
  border: string;
  text: string;
  muted: string;
  accent: string;
  accentBorder: string;
};

const API_BASE = "http://localhost:8000";
type RootName = string;

type StagingStats = {
  images: number;
  videos: number;
  raw: number;
  other: number;
  dirs: number;
  total_files: number;
};

// Detect videos by extension; keep in sync with backend TOML [ext.video]
function looksLikeVideo(name: string): boolean {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext);
}

// Add/replace ?h= on a thumb URL to request a taller JPEG (good for HEIC)
function withHeight(url: string, h: number): string {
  try {
    const u = new URL(url, window.location.origin);
    u.searchParams.set("h", String(h));
    return u.toString();
  } catch {
    const [base, q = ""] = url.split("?");
    const params = new URLSearchParams(q);
    params.set("h", String(h));
    return `${base}?${params.toString()}`;
  }
}

// Discriminated union for preview source (helps TS avoid implicit anys)
type MediaSrc =
  | { kind: "image"; url: string }
  | { kind: "video"; url: string }
  | null;

/* ----------------------------------------------------------------------------
   Component
---------------------------------------------------------------------------- */

export default function StagingView({ theme }: { theme: Theme }) {
  // Routing state (source root + path inside it)
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">("");
  const [path, setPath] = useState<string>("");

  // Data + selection for the grid
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StagingEntry | null>(null);

  // Stats for toolbar pills
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  // EXIF state: table is always visible; "expanded" shows all fields
  const [exifExpanded, setExifExpanded] = useState(false);
  const [exifData, setExifData] = useState<ExifData | null>(null);
  const [exifErr, setExifErr] = useState<string | null>(null);

  // Load available staging roots on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/staging/roots`)
      .then((r) => r.json())
      .then((list: RootName[]) => {
        setRoots(list);
        if (list.length) setRoot((prev) => prev || list[0]);
      })
      .catch((e) => setError(`failed to load roots: ${String(e)}`));
  }, []);

  // Load entries whenever root/path changes
  useEffect(() => {
    if (!root) return;
    const params = new URLSearchParams();
    params.set("root", root);
    if (path) params.set("path", path);

    fetch(`${API_BASE}/api/staging/list?${params.toString()}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((list: StagingEntry[]) => {
        setEntries(list);
        setError(null);
        setSelected(null);       // clear preview when navigating
        setExifExpanded(false);  // collapse EXIF on folder change
      })
      .catch((e) => setError(`failed to list: ${String(e)}`));
  }, [root, path]);

  // Fetch stats for toolbar
  useEffect(() => {
    if (!root) return;
    const params = new URLSearchParams();
    params.set("root", root);
    if (path) params.set("path", path);

    fetch(`${API_BASE}/api/staging/stats?${params.toString()}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((s: StagingStats) => {
        setStats(s);
        setStatsError(null);
      })
      .catch((e) => setStatsError(`failed to load stats: ${String(e)}`));
  }, [root, path]);

  // EXIF: always load basic fields for the selected file (panel is always visible)
  useEffect(() => {
    let cancelled = false;

    if (!selected || selected.is_dir) {
      setExifData(null);
      setExifErr(null);
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        // TODO: replace with real API call (e.g., /api/staging/exif?root=&path=)
        // Provide a few useful basics so collapsed view has content.
        const d: ExifData = {
          Filename: selected.name,
          Modified: selected.mtime ?? "",
          Size: selected.size ?? "",
          Path: selected.rel_path,
        };
        if (!cancelled) setExifData(d);
      } catch (e: unknown) {
        if (!cancelled) setExifErr(toErrorMessage(e));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selected]);

  // Breadcrumbs from current path
  const crumbs = useMemo(() => {
    const parts = path ? path.split("/").filter(Boolean) : [];
    const acc: { name: string; p: string }[] = [];
    let cur = "";
    for (const seg of parts) {
      cur = cur ? `${cur}/${seg}` : seg;
      acc.push({ name: seg, p: cur });
    }
    return acc;
  }, [path]);

  // Navigation helpers
  function goUp() {
    if (!path) return;
    const idx = path.lastIndexOf("/");
    setPath(idx >= 0 ? path.slice(0, idx) : "");
  }
  function openDir(entry: StagingEntry) {
    if (!entry.is_dir) return;
    setPath(entry.rel_path);
  }

  // Compute a preview URL for the right pane (image or video)
  const previewSrc = useMemo<MediaSrc>(() => {
    if (!selected || selected.is_dir) return null;

    if (looksLikeVideo(selected.name)) {
      return { kind: "video", url: selected.media_url ?? "" };
    }
    if (selected.thumb_url) {
      return { kind: "image", url: withHeight(selected.thumb_url, 1200) };
    }
    if (selected.media_url) {
      return { kind: "image", url: selected.media_url };
    }
    return null;
  }, [selected]);

  /* ------------------------------------------------------------------------ */

  return (
    <section
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr",
        gap: 12,
        minHeight: 0,
        minWidth: 0,
        overflow: "hidden",
        height: "100%",
        paddingBottom: 8,
        paddingLeft: 5,
        color: theme.text,
      }}
    >
      {/* Row 1: toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 0,
          flexWrap: "wrap",
          color: theme.text,
          transform: "translateY(8px)",
          width: "100%",
        }}
      >
        {/* Left-side controls */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <label>
            source:&nbsp;
            <select
              value={root}
              onChange={(e) => {
                setRoot(e.target.value);
                setPath("");
              }}
              style={{
                background: theme.surface,
                color: theme.text,
                border: `1px solid ${theme.border}`,
                borderRadius: 8,
                padding: "6px 8px",
              }}
            >
              <option value="" disabled>
                select…
              </option>
              {roots.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>

          <div style={{ color: theme.muted }}>
            path:&nbsp;
            <button
              onClick={() => setPath("")}
              style={{
                border: "none",
                background: "transparent",
                textDecoration: "underline",
                cursor: "pointer",
                padding: 0,
                color: theme.text,
              }}
              title="go to root"
            >
              /
            </button>
            {crumbs.map((c) => (
              <span key={c.p}>
                <span>&nbsp;/&nbsp;</span>
                <button
                  onClick={() => setPath(c.p)}
                  style={{
                    border: "none",
                    background: "transparent",
                    textDecoration: "underline",
                    cursor: "pointer",
                    padding: 0,
                    color: theme.text,
                  }}
                  title={`go to ${c.p}`}
                >
                  {c.name}
                </button>
              </span>
            ))}
          </div>

          <button
            onClick={goUp}
            disabled={!path}
            style={{
              padding: "6px 10px",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
              background: theme.surface,
              color: theme.text,
              cursor: path ? "pointer" : "not-allowed",
              opacity: path ? 1 : 0.5,
            }}
          >
            ↑ up
          </button>
        </div>

        {/* Right-side: counters */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {statsError && <span style={{ color: "#fca5a5", fontSize: 12 }}>{statsError}</span>}

          {[
            { icon: "📷", label: "Images", val: stats?.images },
            { icon: "🎞️", label: "Videos", val: stats?.videos },
            { icon: "📁", label: "Folders", val: stats?.dirs },
            { icon: "🧩", label: "Other", val: stats?.other },
          ].map((p) => (
            <div
              key={p.label}
              title={p.label}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 8px",
                borderRadius: 999,
                border: `1px solid ${theme.border}`,
                background: theme.surface,
                color: theme.text,
                fontSize: 12,
                lineHeight: 1,
              }}
            >
              <span aria-hidden>{p.icon}</span>
              <strong style={{ fontWeight: 600 }}>{p.label}</strong>
              <span style={{ color: theme.muted }}>·</span>
              <span>{p.val ?? "–"}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Row 2: two-column content → left grid, right preview */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr minmax(320px, 38%)",
          gap: 12,
          minHeight: 0,
        }}
      >
        {/* LEFT: items grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 10,
            height: "100%",
            width: "100%",
            overflowY: "auto",
            border: `1px solid ${theme.border}`,
            borderRadius: 10,
            padding: 10,
            boxSizing: "border-box",
            background: theme.surface,
            minHeight: 0,
          }}
        >
          {error && <div style={{ gridColumn: "1 / -1", color: "#fca5a5" }}>{error}</div>}

          {!error && entries.length === 0 && (
            <div
              style={{
                gridColumn: "1 / -1",
                display: "grid",
                placeItems: "center",
                height: "100%",
                color: theme.muted,
                textAlign: "center",
              }}
            >
              <div>
                <div style={{ fontSize: 28, marginBottom: 6 }}>🗂️</div>
                <div>
                  No items here. <strong>Select a folder</strong> to view files.
                </div>
              </div>
            </div>
          )}

          {entries.map((e) => {
            const isDir = e.is_dir;
            const isSelected = !!selected && !isDir && selected.rel_path === e.rel_path;

            // CLICK: folders open; files select for preview
            const onClick = () => {
              if (isDir) openDir(e);
              else setSelected(e);
            };

            return (
              <button
                key={e.rel_path || e.name}
                onClick={onClick}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  textAlign: "left",
                  border: `1px solid ${isSelected ? theme.accentBorder : theme.border}`,
                  boxShadow: isSelected ? `0 0 0 2px ${theme.accentBorder} inset` : undefined,
                  borderRadius: 10,
                  padding: 6,
                  background: theme.cardBg,
                  color: theme.text,
                  cursor: "pointer",
                }}
                aria-label={isDir ? `open folder ${e.name}` : `select file ${e.name}`}
                title={e.rel_path}
              >
                {isDir ? (
                  <div
                    style={{
                      height: 140,
                      borderRadius: 8,
                      border: `1px dashed ${theme.border}`,
                      display: "grid",
                      placeItems: "center",
                      background: "#0e1014",
                      fontSize: 32,
                      color: theme.text,
                    }}
                  >
                    📁
                  </div>
                ) : e.media_url ? (
                  <Thumb src={(e.thumb_url ?? e.media_url)!} alt={e.name} height={140} fit="cover" />
                ) : (
                  <div
                    style={{
                      height: 140,
                      borderRadius: 8,
                      border: `1px dashed ${theme.border}`,
                      display: "grid",
                      placeItems: "center",
                      background: "#0e1014",
                      color: theme.muted,
                      fontSize: 12,
                    }}
                  >
                    preview unavailable
                  </div>
                )}

                <div
                  style={{
                    fontSize: 12,
                    color: theme.text,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {e.name}
                </div>
                <div className="muted" style={{ fontSize: 11, color: theme.muted }}>
                  {e.is_dir ? "folder" : `${e.size ?? ""} bytes`} {e.mtime ? `• ${e.mtime}` : ""}
                </div>
              </button>
            );
          })}
        </div>

        {/* RIGHT: preview panel */}
        <div
          style={{
            border: `1px solid ${theme.border}`,
            borderRadius: 10,
            background: theme.surface,
            padding: 12,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          {/* header */}
          <div style={{ fontSize: 13, marginBottom: 8, color: theme.text }}>
            {selected ? selected.name : "Select a file to preview"}
          </div>

          {/* media */}
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "grid",
              placeItems: "center",
              background: "#0e1014",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {!selected ? (
              <div style={{ color: theme.muted, fontSize: 12 }}>Nothing selected</div>
            ) : selected.is_dir ? (
              <div style={{ color: theme.muted, fontSize: 12 }}>Folders don’t have a preview</div>
            ) : !previewSrc ? (
              <div style={{ color: theme.muted, fontSize: 12 }}>No preview available</div>
            ) : previewSrc.kind === "image" ? (
              <img
                src={previewSrc.url}
                alt={selected.name}
                style={{ maxHeight: "75vh", maxWidth: "100%", objectFit: "contain" }}
              />
            ) : (
              <video src={previewSrc.url} controls style={{ maxHeight: "75vh", maxWidth: "100%" }} />
            )}
          </div>

          {/* footer actions — EXIF on left, Remove on right */}
          <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
            <button
              onClick={() => setExifExpanded((v) => !v)}
              disabled={!selected || selected.is_dir}
              style={{
                padding: "6px 10px",
                borderRadius: 8,
                border: `1px solid ${theme.border}`,
                background: theme.cardBg,
                color: theme.text,
                cursor: !selected || selected.is_dir ? "not-allowed" : "pointer",
                opacity: !selected || selected.is_dir ? 0.5 : 1,
              }}
              title={exifExpanded ? "Hide additional EXIF fields" : "Show all EXIF fields"}
            >
              {exifExpanded ? "Hide EXIF" : "Show EXIF"}
            </button>

            <div style={{ marginLeft: "auto" }} />

            {/* Dangerous action: red visuals to indicate deletion/removal */}
            <button
              disabled={!selected || selected.is_dir}
              onClick={() => selected && !selected.is_dir && alert(`Remove: ${selected.name}`)}
              style={{
                padding: "6px 10px",
                borderRadius: 8,
                border: "1px solid #7f1d1d", // dark red border
                background: "#2a0b0b",       // subtle red-tinted bg
                color: "#ef4444",            // red text
                cursor: !selected || selected.is_dir ? "not-allowed" : "pointer",
                opacity: !selected || selected.is_dir ? 0.5 : 1,
              }}
              title="Delete from Staging (cannot be undone)"
              aria-label="Delete from Staging"
            >
              🗑️ Delete
            </button>
          </div>

          {/* EXIF is always rendered: show a few key fields; expand to show all */}
          <div
            style={{
              marginTop: 10,
              borderTop: `1px solid ${theme.border}`,
              paddingTop: 10,
              fontSize: 12,
              color: theme.text,
              maxHeight: exifExpanded ? 320 : 200,
              overflow: "auto",
            }}
          >
            {!selected || selected.is_dir ? (
              <div style={{ color: theme.muted }}>Select a file to view EXIF</div>
            ) : exifErr ? (
              <div style={{ color: "#fca5a5" }}>Failed to load EXIF: {exifErr}</div>
            ) : !exifData ? (
              <div style={{ color: theme.muted }}>Loading…</div>
            ) : (
              (() => {
                // Show a small “primary” subset when collapsed; everything when expanded.
                const entries = Object.entries(exifData) as Array<[string, ExifValue]>;
                const primaryKeys = new Set([
                  "Filename",
                  "TakenAt",
                  "Modified",
                  "Size",
                  "Path",
                  "GPSLat",
                  "GPSLon",
                  "Make",
                  "Model",
                ]);
                const primary = entries.filter(([k]) => primaryKeys.has(k));
                const visible = exifExpanded ? entries : primary.length ? primary : entries.slice(0, 8);
                const hiddenCount = exifExpanded ? 0 : Math.max(entries.length - visible.length, 0);

                return (
                  <>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                      <tbody>
                        {visible.map(([k, v]) => (
                          <tr key={k} style={{ borderBottom: `1px solid ${theme.border}` }}>
                            <td
                              style={{
                                padding: "6px 8px",
                                color: theme.muted,
                                whiteSpace: "nowrap",
                                verticalAlign: "top",
                              }}
                            >
                              {k}
                            </td>
                            <td style={{ padding: "6px 8px", wordBreak: "break-word" }}>{String(v ?? "")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!exifExpanded && hiddenCount > 0 && (
                      <div style={{ marginTop: 6, color: theme.muted }}>
                        {hiddenCount} more field{hiddenCount === 1 ? "" : "s"} hidden. Click “Show EXIF” to expand.
                      </div>
                    )}
                  </>
                );
              })()
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
