import { useEffect, useMemo, useState } from "react";
import type { StagingEntry } from "../types";
import Preview from "./Preview";

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

export default function StagingView({ theme }: { theme: Theme }) {
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">("");
  const [path, setPath] = useState<string>("");
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  // NEW: stats state for the right-side counter
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Load roots on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/staging/roots`)
      .then((r) => r.json())
      .then((list: RootName[]) => {
        setRoots(list);
        if (list.length) setRoot((prev) => prev || list[0]);
      })
      .catch((e) => setError(`failed to load roots: ${String(e)}`));
  }, []);

  // Load entries whenever root/path changes (previewable files only)
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
      })
      .catch((e) => setError(`failed to list: ${String(e)}`));
  }, [root, path]);

  // NEW: fetch stats (images/videos/raw/other/dirs/total) for current folder
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

  // Build breadcrumb parts from current path
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

  // Navigate up one directory (if possible)
  function goUp() {
    if (!path) return;
    const idx = path.lastIndexOf("/");
    setPath(idx >= 0 ? path.slice(0, idx) : "");
  }

  // Open a directory (no-op for files for now; aside will handle preview later)
  function openDir(entry: StagingEntry) {
    if (!entry.is_dir) return;
    setPath(entry.rel_path);
  }

  return (
    // SECTION ONLY — no aside here anymore
    <section
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr", // Row 1 toolbar, Row 2 content
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

        {/* Right-side: COUNTERS (from backend stats) */}
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {/* Show a subtle error if stats failed */}
          {statsError && (
            <span style={{ color: "#fca5a5", fontSize: 12 }}>{statsError}</span>
          )}

          {/* Always render pills; if stats are null yet, show “–” */}
          <div
            title="Images"
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
            <span aria-hidden>📷</span>
            <strong style={{ fontWeight: 600 }}>Images</strong>
            <span style={{ color: theme.muted }}>·</span>
            <span>{stats ? stats.images : "–"}</span>
          </div>

          <div
            title="Videos"
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
            <span aria-hidden>🎞️</span>
            <strong style={{ fontWeight: 600 }}>Videos</strong>
            <span style={{ color: theme.muted }}>·</span>
            <span>{stats ? stats.videos : "–"}</span>
          </div>

          <div
            title="Subfolders in current directory"
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
            <span aria-hidden>📁</span>
            <strong style={{ fontWeight: 600 }}>Folders</strong>
            <span style={{ color: theme.muted }}>·</span>
            <span>{stats ? stats.dirs : "–"}</span>
          </div>

          <div
            title="Other (junk / unknown)"
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
            <span aria-hidden>🧩</span>
            <strong style={{ fontWeight: 600 }}>Other</strong>
            <span style={{ color: theme.muted }}>·</span>
            <span>{stats ? stats.other : "–"}</span>
          </div>
        </div>
      </div>

      {/* Row 2: scrollable items grid */}
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
        {error && (
          <div style={{ gridColumn: "1 / -1", color: "#fca5a5" }}>
            {error}
          </div>
        )}

        {/* EMPTY STATE */}
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
          const click = () => (isDir ? openDir(e) : undefined);
          return (
            <button
              key={e.rel_path || e.name}
              onClick={click}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
                textAlign: "left",
                border: `1px solid ${theme.border}`,
                borderRadius: 10,
                padding: 6,
                background: theme.cardBg,
                color: theme.text,
                cursor: "pointer",
              }}
              aria-label={isDir ? `open folder ${e.name}` : `open file ${e.name}`}
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
                <Preview src={(e.thumb_url ?? e.media_url)!} alt={e.name} height={140} fit="cover" />
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
    </section>
  );
}
