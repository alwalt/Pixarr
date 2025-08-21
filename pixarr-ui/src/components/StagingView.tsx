import { useEffect, useMemo, useState } from "react";
import type { StagingEntry } from "../types";
import Preview from "./Preview";

// Receive theme from App so colors are centralized and consistent
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

export default function StagingView({ theme }: { theme: Theme }) {
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">("");
  const [path, setPath] = useState<string>("");
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StagingEntry | null>(null);

  // --- data loading ---
  useEffect(() => {
    fetch(`${API_BASE}/api/staging/roots`)
      .then((r) => r.json())
      .then((list: RootName[]) => {
        setRoots(list);
        if (list.length) setRoot((prev) => prev || list[0]);
      })
      .catch((e) => setError(`failed to load roots: ${String(e)}`));
  }, []);

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
        setSelected(null);
      })
      .catch((e) => setError(`failed to list: ${String(e)}`));
  }, [root, path]);

  // --- helpers ---
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

  return (
    // Fill the content row provided by App (no fixed positioning here)
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "3fr 1fr", // 3:1 layout
        gap: 16,
        height: "100%",   // fill App's row 2
        width: "100%",
        minHeight: 0,     // allow children to shrink and scroll
        minWidth: 0,
        overflow: "hidden",
        color: theme.text,
        background: "transparent",
      }}
    >
      {/* Section is a 2-row grid: Row 1 toolbar (auto), Row 2 content (1fr) */}
      <section
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr",
          gap: 12,           // spacing between toolbar and grid
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          paddingBottom: 8,
          paddingLeft: 5,
        }}
      >
        {/* Row 1: toolbar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
            color: theme.text,
          }}
        >
          <label>
            root:&nbsp;
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
              <option value="" disabled>select…</option>
              {roots.map((r) => (
                <option key={r} value={r}>{r}</option>
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

        {/* Row 2: scrollable items grid (fills remaining height) */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 10,
            height: "100%",       // fill the 1fr track (no vh math)
            width: "100%",
            overflowY: "auto",    // scrolling contained here
            border: `1px solid ${theme.border}`,
            borderRadius: 10,
            padding: 10,
            boxSizing: "border-box",
            background: theme.surface,
            minHeight: 0,         // critical for nested grid scrolling
          }}
        >
          {error && (
            <div style={{ gridColumn: "1 / -1", color: "#fca5a5" /* soft red */ }}>
              {error}
            </div>
          )}

          {entries.map((e) => {
            const isDir = e.is_dir;
            const click = () => (isDir ? openDir(e) : setSelected(e));
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
                  /* Use thumbnail in the grid; fallback to media_url */
                  <Preview
                    src={(e.thumb_url ?? e.media_url)!}
                    alt={e.name}
                    height={140}
                    fit="cover"
                  />
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
                  {e.is_dir ? "folder" : `${e.size ?? ""} bytes`}{" "}
                  {e.mtime ? `• ${e.mtime}` : ""}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      {/* Aside: same row height, scrolls internally */}
      <aside
        style={{
          height: "100%",
          overflow: "auto",
          minHeight: 0,
          minWidth: 0,
          background: theme.surface,
          border: `1px solid ${theme.border}`,
          borderRadius: 10,
          padding: 12,
          boxSizing: "border-box",
          color: theme.text,
        }}
      >
        {selected && !selected.is_dir ? (
          <>
            <h2 style={{ marginTop: 0, color: theme.text }}>details</h2>
            {selected.media_url && (
              /* Full-size in details */
              <Preview src={selected.media_url} alt={selected.name} height={320} fit="contain" />
            )}
            <ul style={{ lineHeight: 1.7, color: theme.text }}>
              <li><strong>name:</strong> {selected.name}</li>
              <li><strong>relative path:</strong> {selected.rel_path}</li>
              {selected.size != null && <li><strong>size:</strong> {selected.size} bytes</li>}
              {selected.mtime && <li><strong>modified:</strong> {selected.mtime}</li>}
            </ul>
          </>
        ) : (
          <div className="muted" style={{ color: theme.muted }}>
            select a file to see details
          </div>
        )}
      </aside>
    </div>
  );
}
