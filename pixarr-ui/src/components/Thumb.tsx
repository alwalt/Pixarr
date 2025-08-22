import React from "react";

/**
 * Small, typed thumbnail for grid cards.
 * Renders a <video> for known video extensions; otherwise an <img>.
 * Keep this component simple—it's for grid tiles only.
 */
type Props = {
  src: string;                 // full URL (thumb_url or media_url)
  alt?: string;
  height?: number;             // px
  fit?: "cover" | "contain";
  radius?: number;             // px
};

// Extensions considered video in the grid
const VIDEO_EXT = new Set(["mp4", "mov", "webm", "mkv", "avi"]);

function extOf(url: string): string {
  // ignore query (?h=...) when checking extension
  const q = url.split("?")[0];
  const i = q.lastIndexOf(".");
  return i >= 0 ? q.slice(i + 1).toLowerCase() : "";
}

export default function Thumb({
  src,
  alt = "preview",
  height = 140,
  fit = "cover",
  radius = 8,
}: Props) {
  const isVideo = VIDEO_EXT.has(extOf(src));

  const style: React.CSSProperties = {
    width: "100%",
    height,
    objectFit: fit,
    borderRadius: radius,
    background: "#0e1014",
    display: "block",
  };

  if (isVideo) {
    return (
      <video
        src={src}
        muted
        loop
        playsInline
        controls={false}
        onMouseEnter={(e) => (e.currentTarget as HTMLVideoElement).play()}
        onMouseLeave={(e) => (e.currentTarget as HTMLVideoElement).pause()}
        style={style}
      />
    );
  }

  return <img src={src} alt={alt} loading="lazy" style={style} />;
}
