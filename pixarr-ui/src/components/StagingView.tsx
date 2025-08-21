import { useEffect, useMemo, useState } from "react";
import type { StagingEntry } from "../types";
import Preview from "./Preview";

const API_BASE = "http://localhost:8000";

type RootName = string;

export default function StagingView() {
  const [roots, setRoots] = useState<RootName[]>([]);
  const [root, setRoot] = useState<RootName | "">("");
  const [path, setPath] = useState<string>("");
  const [entries, setEntries] = useState<StagingEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<StagingEntry | null>(null);

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
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
      <section>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <label>
            root:&nbsp;
            <select value={root} onChange={(e) => { setRoot(e.target.value); setPath(""); }}>
              <option value="" disabled>select…</option>
              {roots.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>

          <div style={{ color: "#666" }}>
            path:&nbsp;
            <button onClick={() => setPath("")} style={{ border: "none", background: "transparent", textDecoration: "underline", cursor: "pointer", padding: 0 }} title="go to root">/</button>
            {crumbs.map((c) => (
              <span key={c.p}>
                <span>&nbsp;/&nbsp;</span>
                <button
                  onClick={() => setPath(c.p)}
                  style={{ border: "none", background: "transparent", textDecoration: "underline", cursor: "pointer", padding: 0 }}
                  title={`go to ${c.p}`}
                >
                  {c.name}
                </button>
              </span>
            ))}
          </div>

          <button onClick={goUp} disabled={!path}>↑ up</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10, height: "75vh", overflowY: "auto", border: "1px solid #e9e9e9", borderRadius: 10, padding: 10 }}>
          {error && <div style={{ gridColumn: "1 / -1", color: "crimson" }}>{error}</div>}

          {entries.map((e) => {
            const isDir = e.is_dir;
            const click = () => (isDir ? openDir(e) : setSelected(e));
            return (
              <button
                key={e.rel_path || e.name}
                onClick={click}
                style={{ display: "flex", flexDirection: "column", gap: 6, textAlign: "left", border: "1px solid #e4e4e4", borderRadius: 10, padding: 6, background: "white", cursor: "pointer" }}
                aria-label={isDir ? `open folder ${e.name}` : `open file ${e.name}`}
                title={e.rel_path}
              >
                {isDir ? (
                  <div style={{ height: 140, borderRadius: 8, border: "1px dashed #bbb", display: "grid", placeItems: "center", background: "#fafafa", fontSize: 32 }}>
                    📁
                  </div>
                ) : e.media_url ? (
                  /* Use thumbnail in the grid; fallback to media_url */
                  <Preview src={(e.thumb_url ?? e.media_url)!} alt={e.name} height={140} fit="cover" />
                ) : (
                  <div style={{ height: 140, borderRadius: 8, border: "1px dashed #bbb", display: "grid", placeItems: "center", background: "#fafafa", color: "#999", fontSize: 12 }}>
                    preview unavailable
                  </div>
                )}

                <div style={{ fontSize: 12, color: "#444", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {e.name}
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {e.is_dir ? "folder" : `${e.size ?? ""} bytes`} {e.mtime ? `• {e.mtime}` : ""}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <aside style={{ height: "calc(75vh + 58px)", overflow: "auto" }}>
        {selected && !selected.is_dir ? (
          <>
            <h2 style={{ marginTop: 0 }}>details</h2>
            {selected.media_url && (
              /* Full-size in details */
              <Preview src={selected.media_url} alt={selected.name} height={320} fit="contain" />
            )}
            <ul style={{ lineHeight: 1.7 }}>
              <li><strong>name:</strong> {selected.name}</li>
              <li><strong>relative path:</strong> {selected.rel_path}</li>
              {selected.size != null && <li><strong>size:</strong> {selected.size} bytes</li>}
              {selected.mtime && <li><strong>modified:</strong> {selected.mtime}</li>}
            </ul>
          </>
        ) : (
          <div className="muted">select a file to see details</div>
        )}
      </aside>
    </div>
  );
}
