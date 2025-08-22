import React, { useMemo, useEffect, useState } from "react";
import type { PreviewItem } from "../types";

type ExifValue = string | number | boolean | null | undefined;
type ExifData = Record<string, ExifValue>;
type MediaSrc =
  | { kind: "image"; url: string }
  | { kind: "video"; url: string }
  | null;

type FetchExif = (item: PreviewItem) => Promise<ExifData>;

function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Helpers */

// Decide if an item is a video. Keep in sync with backend TOML [ext.video].
function looksLikeVideo(nameOrExt: string): boolean {
  const s = nameOrExt.toLowerCase();
  const ext = s.startsWith(".") ? s.slice(1) : s.split(".").pop() || "";
  return ["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext);
}

// Add/replace the ?h= param on a thumbnail URL to request a larger JPEG.
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

/** PreviewCanvas: image (incl. HEIC via server JPEG) or video */
function PreviewCanvas({
  item,
  maxHeight = 1200,
}: {
  item: PreviewItem | null;
  maxHeight?: number;
}) {
  const src = useMemo<MediaSrc>(() => {
    if (!item || item.is_dir) return null;

    // Videos → original file
    if (looksLikeVideo(item.ext || item.name)) {
      return { kind: "video", url: item.media_url ?? "" };
    }

    // Images (incl. HEIC) → server JPEG at larger height
    if (item.thumb_url) {
      return { kind: "image", url: withHeight(item.thumb_url, maxHeight) };
    }

    // Fallback: original (may not display for HEIC)
    if (item.media_url) {
      return { kind: "image", url: item.media_url };
    }
    return null;
  }, [item, maxHeight]);

  if (!item) return <div style={{ color: "#6b7280", fontSize: 14 }}>Select an item</div>;
  if (item.is_dir) return <div style={{ color: "#6b7280", fontSize: 14 }}>Folders have no preview</div>;
  if (!src) return <div style={{ color: "#dc2626", fontSize: 14 }}>No preview available</div>;

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "#fafafa" }}>
      {src.kind === "image" ? (
        <img src={src.url} alt={item.name} style={{ maxHeight: "80vh", maxWidth: "100%", objectFit: "contain" }} />
      ) : (
        <video src={src.url} controls style={{ maxHeight: "80vh", maxWidth: "100%" }} />
      )}
    </div>
  );
}

/** PreviewShell: layout + slots so views can inject their own controls */
export default function PreviewShell({
  item,
  fetchExif,
  header,
  sidePanel,
  footer,
  layout = "split",            // NEW: "split" | "stack"
  asideWidth = 320,            // NEW: width of side panel in split mode
  gap = 16,                    // NEW: spacing between areas
}: {
  item: PreviewItem | null;
  fetchExif?: (item: PreviewItem) => Promise<Record<string, unknown>>;
  header?: (ctx: { item: PreviewItem | null }) => React.ReactNode;
  sidePanel?: (ctx: { item: PreviewItem | null; fetchExif?: typeof fetchExif }) => React.ReactNode;
  footer?: (ctx: { item: PreviewItem | null }) => React.ReactNode;
  layout?: "split" | "stack";
  asideWidth?: number | string;
  gap?: number;
}) {
  if (layout === "stack") {
    // Preview on top, panel below (stacked)
    return (
      <div style={{ display: "flex", flexDirection: "column", gap, height: "100%" }}>
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, minHeight: 0 }}>
          <div style={{ marginBottom: 8 }}>{header?.({ item })}</div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <PreviewCanvas item={item} maxHeight={1200} />
          </div>
          <div style={{ marginTop: 12 }}>{footer?.({ item })}</div>
        </div>
        <aside>{sidePanel?.({ item, fetchExif })}</aside>
      </div>
    );
  }

  // Default: side-by-side (current behavior)
  const asideCss =
    typeof asideWidth === "number" ? `${asideWidth}px` : asideWidth;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `1fr ${asideCss}`,
        gap,
        height: "100%",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ marginBottom: 8 }}>{header?.({ item })}</div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <PreviewCanvas item={item} maxHeight={1200} />
        </div>
        <div style={{ marginTop: 12 }}>{footer?.({ item })}</div>
      </div>
      <aside>{sidePanel?.({ item, fetchExif })}</aside>
    </div>
  );
}


/** Optional: built-in EXIF panel */
export function BuiltInExifPanel({
  item,
  fetchExif,
  defaultOpen = false,
}: {
  item: PreviewItem | null;
  fetchExif?: FetchExif;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState<ExifData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !item || !fetchExif) {
      setData(null);
      setErr(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const d = await fetchExif(item);
        if (!cancelled) setData(d);
      } catch (e: unknown) {
        if (!cancelled) setErr(toErrorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, item, fetchExif]);

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "white" }}>
      <button onClick={() => setOpen((o) => !o)} style={{ fontSize: 13, fontWeight: 600 }}>
        {open ? "Hide" : "Show"} EXIF / Metadata
      </button>
      {open && (
        <div style={{ marginTop: 8, fontSize: 12 }}>
          {!item && <div style={{ color: "#6b7280" }}>Select an item</div>}
          {item && !fetchExif && <div style={{ color: "#6b7280" }}>No EXIF fetcher provided.</div>}
          {err && <div style={{ color: "#dc2626" }}>Failed to load EXIF: {err}</div>}
          {data && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {Object.entries(data).map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: "1px solid #f3f4f6" }}>
                    <td style={{ padding: "6px 8px", color: "#6b7280", whiteSpace: "nowrap", verticalAlign: "top" }}>{k}</td>
                    <td style={{ padding: "6px 8px", wordBreak: "break-word" }}>{String(v ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
