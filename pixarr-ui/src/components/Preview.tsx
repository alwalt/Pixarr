import React, { useMemo, useEffect, useState } from "react";
import type { PreviewItem } from "../types";

type ExifValue = string | number | boolean | null | undefined;
type ExifData = Record<string, ExifValue>;
type MediaSrc =
  | { kind: "image"; url: string }
  | { kind: "video"; url: string }
  | null;

function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}


/**
 * Helpers
 */

// Decide if an item is a video. Keep in sync with your backend TOML [ext.video].
function looksLikeVideo(nameOrExt: string): boolean {
  const s = nameOrExt.toLowerCase();
  const ext = s.startsWith(".") ? s.slice(1) : s.split(".").pop() || "";
  return ["mp4", "mov", "m4v", "webm", "mkv", "avi"].includes(ext);
}

// Add/replace the ?h= param on a thumbnail URL to request a larger JPEG from the server.
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

/**
 * PreviewCanvas
 * Renders image (incl. HEIC via server JPEG) or video.
 * This is deliberately "dumb": it only decides which URL to show.
 */
function PreviewCanvas({
  item,
  maxHeight = 1200, // request a taller JPEG for image previews
}: {
  item: PreviewItem | null;
  maxHeight?: number;
}) {
  const src = useMemo<MediaSrc>(() => {
    if (!item || item.is_dir) return null;

    // Videos: use the original file
    if (looksLikeVideo(item.ext || item.name)) {
      return { kind: "video" as const, url: item.media_url ?? "" };
    }

    // Images (incl. HEIC): use server-generated JPEG at a larger height
    if (item.thumb_url) {
      return { kind: "image" as const, url: withHeight(item.thumb_url, maxHeight) };
    }

    // Fallback: try original (may not display for HEIC in the browser)
    if (item.media_url) {
      return { kind: "image" as const, url: item.media_url };
    }

    return null;
  }, [item, maxHeight]);

  if (!item) return <div style={{ color: "#6b7280", fontSize: 14 }}>Select an item</div>;
  if (item.is_dir) return <div style={{ color: "#6b7280", fontSize: 14 }}>Folders have no preview</div>;
  if (!src) return <div style={{ color: "#dc2626", fontSize: 14 }}>No preview available</div>;

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "#fafafa" }}>
      {src.kind === "image" ? (
        <img
          src={src.url}
          alt={item.name}
          style={{ maxHeight: "80vh", maxWidth: "100%", objectFit: "contain" }}
        />
      ) : (
        <video src={src.url} controls style={{ maxHeight: "80vh", maxWidth: "100%" }} />
      )}
    </div>
  );
}

/**
 * PreviewShell
 * Layout + slots that each view can fill with its own controls.
 * This avoids duplicating a bunch of preview code in Staging/Review/Quarantine.
 */
export default function PreviewShell({
  item,
  // pass a fetcher only if your side panel needs EXIF/metadata
  fetchExif,
  header,
  sidePanel,
  footer,
}: {
  item: PreviewItem | null;
  fetchExif?: (item: PreviewItem) => Promise<Record<string, ExifData>>;
  header?: (ctx: { item: PreviewItem | null }) => React.ReactNode;
  sidePanel?: (ctx: { item: PreviewItem | null; fetchExif?: typeof fetchExif }) => React.ReactNode;
  footer?: (ctx: { item: PreviewItem | null }) => React.ReactNode;
}) {
  // Simple 2-column layout without relying on any CSS framework
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16, height: "100%" }}>
      {/* Main (media) column */}
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ marginBottom: 8 }}>{header?.({ item })}</div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <PreviewCanvas item={item} maxHeight={1200} />
        </div>
        <div style={{ marginTop: 12 }}>{footer?.({ item })}</div>
      </div>

      {/* Side panel for EXIF / actions */}
      <aside>{sidePanel?.({ item, fetchExif })}</aside>
    </div>
  );
}

/**
 * Optional: a tiny built-in EXIF panel you can reuse.
 * Use it by rendering <BuiltInExifPanel item={...} fetchExif={...} />
 */
export function BuiltInExifPanel({
  item,
  fetchExif,
  defaultOpen = false,
}: {
  item: PreviewItem | null;
  fetchExif?: (item: PreviewItem) => Promise<ExifData>;
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
    return () => { cancelled = true; };
  }, [open, item, fetchExif]);

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 12, padding: 12, background: "white" }}>
      <button onClick={() => setOpen(o => !o)} style={{ fontSize: 13, fontWeight: 600 }}>
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


// import React from "react";

// type Props = {
//   src: string;
//   alt?: string;
//   fit?: "cover" | "contain";
//   height?: number;
//   radius?: number;
// };

// const IMAGE_EXT = new Set([".jpg",".jpeg",".png",".gif",".webp",".heic",".heif"]);
// const VIDEO_EXT = new Set([".mp4",".mov",".webm",".mkv",".avi"]);

// function extOf(url: string): string {
//   const q = url.split("?")[0];
//   const i = q.lastIndexOf(".");
//   return i >= 0 ? q.slice(i).toLowerCase() : "";
// }

// export default function Preview({ src, alt, fit = "cover", height = 140, radius = 8 }: Props) {
//   const ext = extOf(src);
//   const isVid = VIDEO_EXT.has(ext);

//   const commonStyle: React.CSSProperties = {
//     width: "100%",
//     height,
//     objectFit: fit,
//     borderRadius: radius,
//     background: "#f7f7f7",
//     display: "block",
//   };

//   if (isVid) {
//     return (
//       <video
//         src={src}
//         muted
//         loop
//         playsInline
//         controls={false}
//         onMouseEnter={(e) => (e.currentTarget as HTMLVideoElement).play()}
//         onMouseLeave={(e) => (e.currentTarget as HTMLVideoElement).pause()}
//         style={commonStyle}
//       />
//     );
//   }

//   return (
//     <img
//       src={src}
//       alt={alt ?? "preview"}
//       loading="lazy"
//       onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = "0.3"; }}
//       style={commonStyle}
//     />
//   );
// }


