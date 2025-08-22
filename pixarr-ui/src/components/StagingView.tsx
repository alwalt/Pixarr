import { useEffect, useMemo, useState } from "react";
import type { StagingEntry } from "../types";
import Thumb from "./Thumb";

// Types
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

const API_BASE = "http://localhost:8000";

// Helpers
function looksLikeVideo(name: string): boolean {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext);
}
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
function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export default function StagingView({ theme }: { theme: Theme }) {
  // Routing
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">("");
  const [path, setPath] = useState<string>("");

  // Data + selection
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StagingEntry | null>(null);

  // Stats
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  // EXIF UI
  const [exifExpanded, setExifExpanded] = useState(false);
  const [exifData, setExifData] = useState<ExifData | null>(null);
  const [exifErr, setExifErr] = useState<string | null>(null);

  // Selected → preview DTO (memo to avoid eslint deps warning)
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

  // Roots
  useEffect(() => {
    fetch(`${API_BASE}/api/staging/roots`)
      .then((r) => r.json())
      .then((list: RootName[]) => {
        setRoots(list);
        if (list.length) setRoot((prev) => prev || list[0]);
      })
      .catch((e) => setError(`failed to load roots: ${String(e)}`));
  }, []);

  // Entries
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
        setSelected(null);       // clear preview on nav
        setExifExpanded(false);  // collapse EXIF on nav
      })
      .catch((e) => setError(`failed to list: ${String(e)}`));
  }, [root, path]);

  // Stats
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

  // EXIF fetch (from backend)
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

  // Breadcrumbs
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

  // Nav helpers
  function goUp() {
    if (!path) return;
    const idx = path.lastIndexOf("/");
    setPath(idx >= 0 ? path.slice(0, idx) : "");
  }
  function openDir(entry: StagingEntry) {
    if (!entry.is_dir) return;
    setPath(entry.rel_path);
  }

  // Right-pane preview URL
  const previewSrc = useMemo(() => {
    if (!selectedPreview) return null;
    if (selectedPreview.ext && looksLikeVideo(selectedPreview.ext)) {
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
      {/* toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 0, flexWrap: "wrap", color: theme.text, transform: "translateY(8px)", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <label>
            source:&nbsp;
            <select
              value={root}
              onChange={(e) => { setRoot(e.target.value); setPath(""); }}
              style={{ background: theme.surface, color: theme.text, border: `1px solid ${theme.border}`, borderRadius: 8, padding: "6px 8px" }}
            >
              <option value="" disabled>select…</option>
              {roots.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>

          <div style={{ color: theme.muted }}>
            path:&nbsp;
            <button onClick={() => setPath("")} style={{ border: "none", background: "transparent", textDecoration: "underline", cursor: "pointer", padding: 0, color: theme.text }} title="go to root">/</button>
            {crumbs.map((c) => (
              <span key={c.p}>
                <span>&nbsp;/&nbsp;</span>
                <button onClick={() => setPath(c.p)} style={{ border: "none", background: "transparent", textDecoration: "underline", cursor: "pointer", padding: 0, color: theme.text }} title={`go to ${c.p}`}>{c.name}</button>
              </span>
            ))}
          </div>

          <button onClick={goUp} disabled={!path} style={{ padding: "6px 10px", borderRadius: 8, border: `1px solid ${theme.border}`, background: theme.surface, color: theme.text, cursor: path ? "pointer" : "not-allowed", opacity: path ? 1 : 0.5 }}>
            ↑ up
          </button>
        </div>

        {/* counters */}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {statsError && <span style={{ color: "#fca5a5", fontSize: 12 }}>{statsError}</span>}
          {[
            { icon: "📷", label: "Images", val: stats?.images },
            { icon: "🎞️", label: "Videos", val: stats?.videos },
            { icon: "📁", label: "Folders", val: stats?.dirs },
            { icon: "🧩", label: "Other",  val: stats?.other },
          ].map((p) => (
            <div key={p.label} title={p.label} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 8px", borderRadius: 999, border: `1px solid ${theme.border}`, background: theme.surface, color: theme.text, fontSize: 12, lineHeight: 1 }}>
              <span aria-hidden>{p.icon}</span>
              <strong style={{ fontWeight: 600 }}>{p.label}</strong>
              <span style={{ color: theme.muted }}>·</span>
              <span>{p.val ?? "–"}</span>
            </div>
          ))}
        </div>
      </div>

      {/* content: grid + preview */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr minmax(360px, 40%)", gap: 12, minHeight: 0 }}>
        {/* LEFT: grid */}
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
            <div style={{ gridColumn: "1 / -1", display: "grid", placeItems: "center", height: "100%", color: theme.muted, textAlign: "center" }}>
              <div>
                <div style={{ fontSize: 28, marginBottom: 6 }}>🗂️</div>
                <div>No items here. <strong>Select a folder</strong> to view files.</div>
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
                  <div style={{ height: 140, borderRadius: 8, border: `1px dashed ${theme.border}`, display: "grid", placeItems: "center", background: "#0e1014", fontSize: 32, color: theme.text }}>
                    📁
                  </div>
                ) : e.media_url ? (
                  <Thumb src={(e.thumb_url ?? e.media_url)!} alt={e.name} height={140} fit="cover" />
                ) : (
                  <div style={{ height: 140, borderRadius: 8, border: `1px dashed ${theme.border}`, display: "grid", placeItems: "center", background: "#0e1014", color: theme.muted, fontSize: 12 }}>
                    preview unavailable
                  </div>
                )}

                <div style={{ fontSize: 12, color: theme.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
                onClick={() => setExifExpanded(v => !v)}
                disabled={!selectedPreview}
                style={{
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: `1px solid ${theme.border}`,
                  background: theme.cardBg,
                  color: theme.text,
                  cursor: selectedPreview ? "pointer" : "not-allowed",
                  opacity: selectedPreview ? 1 : 0.5,
                }}
                title={exifExpanded ? "Hide additional EXIF fields" : "Show all EXIF fields"}
              >
                {exifExpanded ? "Hide EXIF" : "Show EXIF"}
              </button>

              <div style={{ marginLeft: "auto" }} />

              <button
                disabled={!selectedPreview}
                onClick={() => selectedPreview && alert(`Delete: ${selectedPreview.name}`)}
                style={{
                  padding: "6px 10px",
                  borderRadius: 8,
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
                    "Basic:Filename", "Basic:Modified", "Basic:Size", "Basic:Path",
                    "EXIF:DateTimeOriginal", "EXIF:CreateDate", "QuickTime:CreateDate",
                    "EXIF:Make", "EXIF:Model", "EXIF:GPSLatitude", "EXIF:GPSLongitude",
                  ]);
                  const primary = entries.filter(([k]) => primaryKeys.has(k));
                  const visible = exifExpanded ? entries : (primary.length ? primary : entries.slice(0, 8));
                  const hiddenCount = exifExpanded ? 0 : Math.max(entries.length - visible.length, 0);

                  return (
                    <>
                      <table style={{ width: "100%", borderCollapse: "collapse" }}>
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
