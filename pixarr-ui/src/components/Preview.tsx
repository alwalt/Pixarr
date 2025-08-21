import React from "react";

type Props = {
  src: string;
  alt?: string;
  fit?: "cover" | "contain";
  height?: number;
  radius?: number;
};

const IMAGE_EXT = new Set([".jpg",".jpeg",".png",".gif",".webp",".heic",".heif"]);
const VIDEO_EXT = new Set([".mp4",".mov",".webm",".mkv",".avi"]);

function extOf(url: string): string {
  const q = url.split("?")[0];
  const i = q.lastIndexOf(".");
  return i >= 0 ? q.slice(i).toLowerCase() : "";
}

export default function Preview({ src, alt, fit = "cover", height = 140, radius = 8 }: Props) {
  const ext = extOf(src);
  const isVid = VIDEO_EXT.has(ext);

  const commonStyle: React.CSSProperties = {
    width: "100%",
    height,
    objectFit: fit,
    borderRadius: radius,
    background: "#f7f7f7",
    display: "block",
  };

  if (isVid) {
    return (
      <video
        src={src}
        muted
        loop
        playsInline
        controls={false}
        onMouseEnter={(e) => (e.currentTarget as HTMLVideoElement).play()}
        onMouseLeave={(e) => (e.currentTarget as HTMLVideoElement).pause()}
        style={commonStyle}
      />
    );
  }

  return (
    <img
      src={src}
      alt={alt ?? "preview"}
      loading="lazy"
      onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = "0.3"; }}
      style={commonStyle}
    />
  );
}
