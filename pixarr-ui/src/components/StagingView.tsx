// src/components/StagingView.tsx
// Staging browser (sources → folders → media) with right‑hand preview + EXIF.
// Notes:
// - Keeps the UI snappy and stateful (root/path/selection/scroll persisted in App).
// - Fetches roots, directory entries, and directory stats from the FastAPI backend.
// - Sync button calls POST /api/staging/sync/icloud and, on success, triggers a light refresh.

import { useEffect, useMemo, useRef, useState } from "react";
import type { StagingEntry } from "../types";
import Thumb from "./Thumb";

/* ========================================================================== */
/*  Types & constants                                                         */
/* ========================================================================== */

type Theme = {
  appBg: string; headerBg: string; surface: string; cardBg: string;
  border: string; text: string; muted: string; accent: string; accentBorder: string;
};
type RootName = string;

type StagingStats = {
  images: number; videos: number; raw: number; other: number; dirs: number; total_files: number;
};

type ExifValue = string | number | boolean | null | undefined;
type ExifData = Record<string, ExifValue>;

/** Matches App.tsx (duplicated locally to avoid import cycles) */
export type StagingSavedState = {
  root: string;
  path: string;
  selectedRel?: string;
  scrollTop?: number;
};

const API_BASE = "http://localhost:8000";

/* ========================================================================== */
/*  Small helpers                                                             */
/* ========================================================================== */

/** Decide if extension is a video; used for preview rendering. */
function looksLikeVideo(name: string): boolean {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext);
}

/** Append/override height query to thumb URLs (server scales). */
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

/** Normalize thrown values → string messages for UI. */
function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/* ========================================================================== */
/*  Component                                                                 */
/* ========================================================================== */

export default function StagingView({
  theme,
  savedState,
  onSavedStateChange,
}: {
  theme: Theme;
  /** Provided by App so your place survives tab switches */
  savedState: StagingSavedState;
  /** Call this whenever root/path/selection/scroll changes */
  onSavedStateChange: (s: StagingSavedState) => void;
}) {
  /* ------------------------------------------------------------------------ */
  /*  Local state (routing, data, selection)                                  */
  /* ------------------------------------------------------------------------ */

  // Source roots; do NOT auto-pick a root (respect savedState or "").
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">(savedState.root || "");
  const [path, setPath] = useState<string>(savedState.path || "");

  // Directory listing + selection
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StagingEntry | null>(null);

  // Directory stats
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  // EXIF panel
  const [exifExpanded, setExifExpanded] = useState(false);
  const [exifData, setExifData] = useState<ExifData | null>(null);
  const [exifErr, setExifErr] = useState<string | null>(null);

  // Scroll persistence
  const gridRef = useRef<HTMLDivElement | null>(null);
  const scrollRAF = useRef<number | null>(null);

  // Lightweight “refresh” tick (used after Sync completes to refetch list/stats)
  const [refreshTick, setRefreshTick] = useState(0);

  /* ------------------------------------------------------------------------ */
  /*  Derived selection → preview DTO                                         */
  /* ------------------------------------------------------------------------ */

  const selectedPreview = useMemo(() => {
    if (!selected || selected.is_dir) return null;
    return {
      name: selected.name,
      is_dir: false,
      rel_path: selected.rel_path,
      media_url: selected.media_url ?? undefined,
      thumb_url: selected.thumb_url ?? undefined,
      ext: selected.name.split(".").pop()?.toLowerCase(),
    };
  }, [selected]);

  /* ------------------------------------------------------------------------ */
  /*  Fetch: roots                                                            */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {
    fetch(`${API_BASE}/api/staging/roots`)
      .then((r) => r.json())
      .then((list: RootName[]) => {
        setRoots(list);
        // Note: do NOT auto-select a root here.
      })
      .catch((e) => setError(`failed to load roots: ${String(e)}`));
  }, []);

  /* ------------------------------------------------------------------------ */
  /*  Fetch: entries (depends on root, path, refreshTick)                     */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {
    if (!root) return; // require explicit selection
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
        // UX: collapse EXIF on nav changes; we try to restore selection separately
        setExifExpanded(false);
      })
      .catch((e) => setError(`failed to list: ${String(e)}`));
  }, [root, path, refreshTick]);

  /* ------------------------------------------------------------------------ */
  /*  Fetch: stats (same dependencies as entries)                             */
  /* ------------------------------------------------------------------------ */

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
  }, [root, path, refreshTick]);

  /* ------------------------------------------------------------------------ */
  /*  Fetch: EXIF (depends on current selection)                              */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {
    let cancelled = false;

    if (!selectedPreview || !root) {
      setExifData(null);
      setExifErr(null);
      return () => { cancelled = true; };
    }

    const params = new URLSearchParams({
      root,
      path: selectedPreview.rel_path,
      compact: "true",
    });

    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/staging/exif?${params.toString()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = (await r.json()) as ExifData;
        if (!cancelled) {
          setExifData(d);
          setExifErr(null);
        }
      } catch (e: unknown) {
        if (!cancelled) setExifErr(toErrorMessage(e));
      }
    })();

    return () => { cancelled = true; };
  }, [selectedPreview, root]);

  /* ------------------------------------------------------------------------ */
  /*  Restore selection + scroll after entries load                            */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {
    // Try to restore the previously selected item by rel_path
    if (savedState?.selectedRel && entries.length) {
      const match = entries.find((e) => !e.is_dir && e.rel_path === savedState.selectedRel) || null;
      setSelected(match);
    }
    // Restore scroll position after the grid renders
    const t = setTimeout(() => {
      if (gridRef.current && typeof savedState?.scrollTop === "number") {
        gridRef.current.scrollTop = savedState.scrollTop!;
      }
    }, 0);
    return () => clearTimeout(t);
  }, [entries, savedState?.selectedRel, savedState?.scrollTop]);

  /* ------------------------------------------------------------------------ */
  /*  Bubble state up whenever root/path/selection change                      */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {
    onSavedStateChange({
      root: root || "",
      path,
      selectedRel: selected?.rel_path,
      scrollTop: gridRef.current?.scrollTop || 0,
    });
  }, [root, path, selected, onSavedStateChange]);

  /* ------------------------------------------------------------------------ */
  /*  Throttle scroll-to-parent updates via rAF                                */
  /* ------------------------------------------------------------------------ */

  function onGridScroll() {
    if (scrollRAF.current) cancelAnimationFrame(scrollRAF.current);
    scrollRAF.current = requestAnimationFrame(() => {
      onSavedStateChange({
        root: root || "",
        path,
        selectedRel: selected?.rel_path,
        scrollTop: gridRef.current?.scrollTop || 0,
      });
    });
  }
  useEffect(() => {
    return () => {
      if (scrollRAF.current) cancelAnimationFrame(scrollRAF.current);
    };
  }, []);

  /* ------------------------------------------------------------------------ */
  /*  Breadcrumbs + simple nav helpers                                         */
  /* ------------------------------------------------------------------------ */

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

  function goUp() {
    if (!path) return;
    const idx = path.lastIndexOf("/");
    setPath(idx >= 0 ? path.slice(0, idx) : "");
  }
  function openDir(entry: StagingEntry) {
    if (!entry.is_dir) return;
    setPath(entry.rel_path);
  }

  /* ------------------------------------------------------------------------ */
  /*  Right‑pane preview URL                                                   */
  /* ------------------------------------------------------------------------ */

  const previewSrc = useMemo(() => {
    if (!selectedPreview) return null;

    // Pass the full filename to looksLikeVideo (fixes brittle ext handling)
    if (looksLikeVideo(selectedPreview.name)) {
      return { kind: "video" as const, url: selectedPreview.media_url ?? "" };
    }
    if (selectedPreview.thumb_url) {
      return { kind: "image" as const, url: withHeight(selectedPreview.thumb_url, 1200) };
    }
    if (selectedPreview.media_url) {
      return { kind: "image" as const, url: selectedPreview.media_url };
    }
    return null;
  }, [selectedPreview]);

  /* ------------------------------------------------------------------------ */
  /*  Move-to-Review split button (UI only — backend TBD)                      */
  /* ------------------------------------------------------------------------ */

  type MoveMode = "folder" | "folderRecursive" | "root" | "allRoots" | "selected";
  const [moveMode, setMoveMode] = useState<MoveMode>("folder"); // remember last-used
  const [dryRun, setDryRun] = useState<boolean>(true);
  const [moveMenuOpen, setMoveMenuOpen] = useState(false);
  const moveMenuRef = useRef<HTMLDivElement | null>(null);

  // Close the menu when clicking outside
  useEffect(() => {
    function onDocDown(e: MouseEvent) {
      if (!moveMenuRef.current) return;
      if (!moveMenuRef.current.contains(e.target as Node)) setMoveMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, []);

  function labelForMode(m: MoveMode): string {
    switch (m) {
      case "folder": return "This folder";
      case "folderRecursive": return "This folder + subfolders";
      case "root": return `Entire root${root ? ` (${root})` : ""}`;
      case "allRoots": return "All roots";
      case "selected": return "Selected item";
      default: {
        const _exhaustive: never = m;
        return String(_exhaustive);
      }
    }
  }

  const primaryDisabled =
    (moveMode === "folder" || moveMode === "folderRecursive" || moveMode === "root") ? !root :
    (moveMode === "selected" ? !selectedPreview : false);

  async function runMove(mode?: MoveMode) {
    const m = mode ?? moveMode;

    // Guard rails
    if ((m === "folder" || m === "folderRecursive" || m === "root") && !root) {
      alert("Pick a source first.");
      return;
    }
    if (m === "selected" && !selectedPreview) {
      alert("Select a file first.");
      return;
    }

    // Payload you’ll POST when backend is ready
    const payload: Record<string, unknown> = {
      mode: m,
      root: root || null,
      path: path || "",
      recursive: m === "folderRecursive" || m === "root",
      allRoots: m === "allRoots",
      selectedRel: m === "selected" ? selectedPreview?.rel_path : null,
      dryRun,
    };

    const msg =
      m === "folder" ? `Move: this folder only?\n${root}/${path || ""}`
      : m === "folderRecursive" ? `Move: this folder + subfolders?\n${root}/${path || ""}`
      : m === "root" ? `Move entire root "${root}"?`
      : m === "allRoots" ? "Move ALL configured roots?"
      : `Move selected item?\n${selectedPreview?.name || ""}`;

    if (!window.confirm(`${msg}\n\n${dryRun ? "(Dry run)" : "(Write mode)"}`)) return;

    try {
      // TODO: wire to your backend ingest endpoint
      console.log("Would POST /api/ingest/run", payload);
      alert(`Queued ingest:\n${JSON.stringify(payload, null, 2)}`);
    } catch (e) {
      alert(`Failed to start ingest: ${toErrorMessage(e)}`);
    } finally {
      setMoveMenuOpen(false);
    }
  }

  /* ------------------------------------------------------------------------ */
  /*  Sync button → call backend icloud sync (single-run lock on server)       */
  /* ------------------------------------------------------------------------ */

  async function runSync() {
    try {
      const res = await fetch(`${API_BASE}/api/staging/sync/icloud`, { method: "POST" });
      const data = await res.json();

      if (data.status === "busy") {
        alert("Sync is already running — please wait.");
        return;
      }
      if (data.status === "error") {
        alert(`Config error:\n${data.msg}`);
        return;
      }

      if (data.exit_code === 0) {
        alert("iCloud sync completed!");
        // Nudge list/stats to refresh (especially helpful for icloud root)
        setRefreshTick((t) => t + 1);
      } else {
        const msg = data.stderr || data.stdout || "No output";
        alert(`Sync failed (code ${data.exit_code}):\n${msg}`);
      }
    } catch (e) {
      alert(`Failed to trigger sync: ${String(e)}`);
    }
  }

  /* ------------------------------------------------------------------------ */
  /*  Render                                                                   */
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
      {/* =============================== Toolbar ============================== */}
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
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", paddingBottom: 4 }}>
          {/* Source select (styled like action buttons) */}
          <label style={{ position: "relative", display: "inline-block" }}>
            Source:&nbsp;
            <div style={{ position: "relative", display: "inline-block", width: 180 }}>
              <select
                className="px-pill px-action px-select"
                value={root}
                onChange={(e) => {
                  setRoot(e.target.value);
                  setPath(""); // changing source resets path
                }}
              >
                <option value="" disabled>select…</option>
                {roots.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              {/* caret overlay */}
              <span className="px-caret">▾</span>
            </div>
          </label>

          {/* SYNC */}
          <button
            onClick={runSync}
            className="px-pill px-action"
            style={{ padding: "0 8px" }} // keep height via class, adjust padding only
            title="Sync staging folders (icloudpd)"
          >
            🔄 Sync
          </button>

          {/* MOVE TO REVIEW: split button with scope menu */}
          <div ref={moveMenuRef} style={{ position: "relative", display: "inline-flex" }}>
            {/* Primary (runs last-used scope) */}
            <button
              onClick={() => runMove()}
              disabled={primaryDisabled}
              className="px-pill px-action px-split-left"
              style={{
                padding: "0 10px",
                cursor: primaryDisabled ? "not-allowed" : "pointer",
                opacity: primaryDisabled ? 0.6 : 1,
                background: "var(--bg-card)",
              }}
              title={`Run: ${labelForMode(moveMode)}${dryRun ? " (dry run)" : ""}`}
            >
              📤 Move to Review
            </button>

            {/* Caret (opens the menu) */}
            <button
              onClick={() => setMoveMenuOpen((v) => !v)}
              className="px-pill px-action px-split-right"
              aria-label="Choose scope for Move to Review"
              title="Choose scope"
            >
              ▾
            </button>

            {/* Dropdown */}
            {moveMenuOpen && (
              <div
                role="menu"
                style={{
                  position: "absolute",
                  top: "calc(100% + 6px)",
                  left: 0,
                  minWidth: 260,
                  background: theme.cardBg,
                  color: theme.text,
                  border: `1px solid ${theme.border}`,
                  borderRadius: 10,
                  padding: 6,
                  boxShadow: "0 6px 24px rgba(0,0,0,0.4)",
                  zIndex: 20,
                }}
              >
                <div style={{ padding: "6px 8px", color: theme.muted, fontSize: 12 }}>Scope</div>

                <button
                  role="menuitem"
                  onClick={() => { setMoveMode("folder"); runMove("folder"); }}
                  disabled={!root}
                  className="px-pill px-action"
                  style={{ width: "100%", textAlign: "left", marginBottom: 6, background: "transparent" }}
                  title="Only the current folder"
                >
                  📂 This folder only
                </button>

                <button
                  role="menuitem"
                  onClick={() => { setMoveMode("folderRecursive"); runMove("folderRecursive"); }}
                  disabled={!root}
                  className="px-pill px-action"
                  style={{ width: "100%", textAlign: "left", marginBottom: 6, background: "transparent" }}
                  title="Current folder and all subfolders"
                >
                  🧭 This folder + subfolders
                </button>

                <button
                  role="menuitem"
                  onClick={() => { setMoveMode("root"); runMove("root"); }}
                  disabled={!root}
                  className="px-pill px-action"
                  style={{ width: "100%", textAlign: "left", marginBottom: 6, background: "transparent" }}
                  title="Everything under the selected root"
                >
                  🏷️ Entire root {root ? `(${root})` : ""}
                </button>

                <button
                  role="menuitem"
                  onClick={() => { setMoveMode("allRoots"); runMove("allRoots"); }}
                  className="px-pill px-action"
                  style={{ width: "100%", textAlign: "left", marginBottom: 6, background: "transparent" }}
                  title="Every configured root (icloud, pc, sdcard, …)"
                >
                  🌐 All roots
                </button>

                <button
                  role="menuitem"
                  onClick={() => { setMoveMode("selected"); runMove("selected"); }}
                  disabled={!selectedPreview}
                  className="px-pill px-action"
                  style={{ width: "100%", textAlign: "left", marginBottom: 6, background: "transparent" }}
                  title="Only the selected file"
                >
                  🔎 Selected item only
                </button>

                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 8px", marginTop: 4 }}>
                  <input
                    id="dry-run"
                    type="checkbox"
                    checked={dryRun}
                    onChange={(e) => setDryRun(e.target.checked)}
                  />
                  <label htmlFor="dry-run" style={{ fontSize: 13, color: theme.text, cursor: "pointer" }}>
                    Dry run
                  </label>
                  <div style={{ marginLeft: "auto", color: theme.muted, fontSize: 12 }}>
                    Last: <em>{labelForMode(moveMode)}</em>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Breadcrumbs + Up */}
          <div style={{ color: theme.muted }}>
            Path:&nbsp;
            <button
              onClick={() => setPath("")}
              style={{
                padding: "4px 8px",
                border: "none",
                background: "transparent",
                textDecoration: "underline",
                cursor: "pointer",
                color: theme.text,
                lineHeight: "28px",
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
                    padding: "4px 8px",
                    border: "none",
                    background: "transparent",
                    textDecoration: "underline",
                    cursor: "pointer",
                    color: theme.text,
                    lineHeight: "28px",
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
            className="px-pill px-action"
            style={{
              padding: "0 8px",
              cursor: path ? "pointer" : "not-allowed",
              opacity: path ? 1 : 0.5,
              background: "var(--bg-card)",
            }}
            title="Up one level"
            aria-label="Up one level"
          >
            ↑
          </button>
        </div>

        {/* counters */}
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
              className="px-pill"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                lineHeight: 1,
                background: "var(--bg-surface)",
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

      {/* ======================== Content: grid + preview ===================== */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr minmax(360px, 40%)", gap: 8, minHeight: 0, paddingBottom: 0 }}>
        {/* LEFT: grid */}
        <div
          ref={gridRef} // capture scroll for persistence
          onScroll={onGridScroll}
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
            alignItems: "start",
          }}
        >
          {error && <div style={{ gridColumn: "1 / -1", color: "#fca5a5" }}>{error}</div>}

          {!error && (!root || entries.length === 0) && (
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
                {root ? (
                  <div>
                    No items here. <strong>Select a folder</strong> to view files.
                  </div>
                ) : (
                  <div>
                    <strong>Select a source</strong> to begin.
                  </div>
                )}
              </div>
            </div>
          )}

          {entries.map((e) => {
            const isDir = e.is_dir;
            const onClick = () => (isDir ? openDir(e) : setSelected(e));
            return (
              <button
                key={e.rel_path || e.name}
                onClick={onClick}
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

        {/* RIGHT: preview panel (image/video on top, EXIF below) */}
        <div
          style={{
            border: `1px solid ${theme.border}`,
            borderRadius: 10,
            background: theme.surface,
            padding: 12,
            display: "grid",
            gridTemplateRows: "auto minmax(220px, 1fr) auto",
            gap: 10,
            minHeight: 0,
          }}
        >
          {/* header */}
          <div style={{ fontSize: 13, color: theme.text }}>
            {selectedPreview ? selectedPreview.name : "Select a file to preview"}
          </div>

          {/* media */}
          <div
            style={{
              minHeight: 0,
              display: "grid",
              placeItems: "center",
              background: "#0e1014",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {!selectedPreview ? (
              <div style={{ color: theme.muted, fontSize: 12 }}>Nothing selected</div>
            ) : !previewSrc ? (
              <div style={{ color: theme.muted, fontSize: 12 }}>No preview available</div>
            ) : previewSrc.kind === "image" ? (
              <img
                src={previewSrc.url}
                alt={selectedPreview.name}
                style={{ maxHeight: "70vh", maxWidth: "100%", objectFit: "contain" }}
              />
            ) : (
              <video src={previewSrc.url} controls style={{ maxHeight: "70vh", maxWidth: "100%" }} />
            )}
          </div>

          {/* actions + EXIF */}
          <div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <button
                onClick={() => setExifExpanded((v) => !v)}
                disabled={!selectedPreview}
                className="px-pill px-action"
                style={{
                  padding: "0 10px",
                  cursor: selectedPreview ? "pointer" : "not-allowed",
                  opacity: selectedPreview ? 1 : 0.5,
                  background: "var(--bg-card)",
                }}
                title={exifExpanded ? "Hide additional EXIF fields" : "Show all EXIF fields"}
              >
                {exifExpanded ? "Hide EXIF" : "Show EXIF"}
              </button>

              <div style={{ marginLeft: "auto" }} />

              <button
                disabled={!selectedPreview}
                onClick={() => selectedPreview && alert(`Delete: ${selectedPreview.name}`)}
                className="px-pill"
                style={{
                  padding: "0 10px",
                  border: "1px solid #7f1d1d",
                  background: "#2a0b0b",
                  color: "#ef4444",
                  cursor: selectedPreview ? "pointer" : "not-allowed",
                  opacity: selectedPreview ? 1 : 0.5,
                }}
                title="Delete from Staging (cannot be undone)"
                aria-label="Delete from Staging"
              >
                🗑️ Delete
              </button>
            </div>

            <div
              style={{
                borderTop: `1px solid ${theme.border}`,
                paddingTop: 10,
                fontSize: 12,
                color: theme.text,
                maxHeight: exifExpanded ? 320 : 200,
                overflow: "auto",
              }}
            >
              {!selectedPreview ? (
                <div style={{ color: theme.muted }}>Select a file to view EXIF</div>
              ) : exifErr ? (
                <div style={{ color: "#fca5a5" }}>Failed to load EXIF: {exifErr}</div>
              ) : !exifData ? (
                <div style={{ color: theme.muted }}>Loading…</div>
              ) : (
                (() => {
                  const entries = Object.entries(exifData) as Array<[string, ExifValue]>;
                  const primaryKeys = new Set([
                    "Basic:Filename",
                    "Basic:Modified",
                    "Basic:Size",
                    "Basic:Path",
                    "EXIF:DateTimeOriginal",
                    "EXIF:CreateDate",
                    "QuickTime:CreateDate",
                    "EXIF:Make",
                    "EXIF:Model",
                    "EXIF:GPSLatitude",
                    "EXIF:GPSLongitude",
                  ]);
                  const primary = entries.filter(([k]) => primaryKeys.has(k));
                  const visible = exifExpanded ? entries : primary.length ? primary : entries.slice(0, 8);
                  const hiddenCount = exifExpanded ? 0 : Math.max(entries.length - visible.length, 0);

                  return (
                    <>
                      <table className="exif-table" style={{ width: "100%", borderCollapse: "collapse" }}>
                        <tbody>
                          {visible.map(([k, v]) => (
                            <tr key={k} style={{ borderBottom: `1px solid ${theme.border}` }}>
                              <td style={{ padding: "6px 8px", color: theme.muted, whiteSpace: "nowrap", verticalAlign: "top" }}>{k}</td>
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
      </div>
    </section>
  );
}
