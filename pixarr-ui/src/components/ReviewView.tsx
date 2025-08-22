import { useEffect, useMemo, useState } from "react";
import type { MediaItem, MediaListResponse } from "../types";
import Preview from "./Preview";

// FastAPI endpoint
const REVIEW_API = "http://localhost:8000/api/review";

// Format capture time safely
function fmtTakenAt(iso?: string | null): string {
  if (!iso) return "unknown capture time";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "unknown capture time" : d.toLocaleString();
}

export default function ReviewView() {
  const [files, setFiles] = useState<MediaItem[]>([]);
  const [selected, setSelected] = useState<MediaItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    fetch(REVIEW_API)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: MediaListResponse) => {
        if (!cancelled) setFiles(data);
      })
      .catch((err) => !cancelled && setError(`failed to load review: ${String(err)}`));
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    if (!filter.trim()) return files;
    const f = filter.toLowerCase();
    return files.filter((x) =>
      [x.id, x.canonical_path, x.taken_at ?? "", String(x.gps_lat ?? ""), String(x.gps_lon ?? "")]
        .join(" ")
        .toLowerCase()
        .includes(f)
    );
  }, [files, filter]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
      <section>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <input
            type="search"
            placeholder="filter by id/path/date"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ flex: 1, padding: "8px 10px", border: "1px solid #ccc", borderRadius: 8 }}
          />
          <button onClick={() => setFilter("")} style={{ padding: "8px 12px", border: "1px solid #ccc", borderRadius: 8, background: "#fff" }}>
            clear
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10, height: "75vh", overflowY: "auto", border: "1px solid #e9e9e9", borderRadius: 10, padding: 10 }}>
          {error && <div style={{ gridColumn: "1 / -1", color: "crimson" }}>{error}</div>}
          {!error && filtered.length === 0 && <div style={{ gridColumn: "1 / -1", color: "#666" }}>no matching items</div>}

          {filtered.map((file) => (
            <button
              key={file.id}
              onClick={() => setSelected(file)}
              style={{ display: "flex", flexDirection: "column", gap: 6, textAlign: "left", border: "1px solid #e4e4e4", borderRadius: 10, padding: 6, background: "white", cursor: "pointer" }}
              aria-label={`open details for ${file.id}`}
            >
              {/* Use thumbnail in the grid */}
              <Preview src={file.thumb_url ?? file.media_url} alt={file.id} height={140} fit="cover" />
              <div style={{ fontSize: 12, color: "#444" }}>{fmtTakenAt(file.taken_at)}</div>
              <div title={file.canonical_path} style={{ fontSize: 12, color: "#777", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file.canonical_path}
              </div>
            </button>
          ))}
        </div>
      </section>

      <aside style={{ height: "calc(75vh + 58px)", overflow: "auto" }}>
        {selected ? (
          <>
            <h2 style={{ marginTop: 0 }}>details</h2>
            {/* Full-size preview in details */}
            <Preview src={selected.media_url} alt={selected.id} height={320} fit="contain" />
            <ul style={{ lineHeight: 1.7 }}>
              <li><strong>id:</strong> {selected.id}</li>
              <li><strong>path:</strong> {selected.canonical_path}</li>
              <li><strong>taken at:</strong> {fmtTakenAt(selected.taken_at)}</li>
              {(selected.gps_lat ?? null) !== null && (selected.gps_lon ?? null) !== null && (
                <li><strong>location:</strong> {selected.gps_lat}, {selected.gps_lon}</li>
              )}
            </ul>
          </>
        ) : (
          <div className="muted">select a file from the grid to see metadata</div>
        )}
      </aside>
    </div>
  );
}
