import { useState } from "react";
import ReviewView from "./components/ReviewView";
import StagingView from "./components/StagingView";

type Tab = "staging" | "review";

// Centralized dark theme tokens (tweak here to change the whole app)
const theme = {
  appBg: "#0b0c0f",       // page background (near-black)
  headerBg: "#0f1115",    // header background (slightly lighter)
  surface: "#111318",     // panels / surfaces
  cardBg: "#12151b",      // cards inside surfaces
  border: "#23262e",      // subtle dark border
  text: "#e5e7eb",        // primary text (off-white)
  muted: "#9ca3af",       // secondary/muted text
  accent: "#2563eb",      // brand accent (blue)
  accentBorder: "#3b82f6",// accent border (slightly lighter than accent)
};

export default function App() {
  const [tab, setTab] = useState<Tab>("staging");

  // Button factory for tabs
  const tabBtn = (t: Tab, label: string) => {
    const active = tab === t;
    return (
      <button
        onClick={() => setTab(t)}
        style={{
          padding: "8px 14px",
          borderRadius: 8,
          border: `1px solid ${active ? theme.accentBorder : theme.border}`,
          background: active ? theme.accent : theme.surface,
          color: active ? "#ffffff" : theme.text,
          fontWeight: active ? 600 : 500,
          cursor: "pointer",
          minWidth: 96,
        }}
      >
        {label}
      </button>
    );
  };

  return (
    // App owns the viewport: Row 1 header (auto), Row 2 content (1fr)
    <div
      id="app-root"
      style={{
        position: "fixed",
        inset: 0, // top/right/bottom/left: 0
        display: "grid",
        gridTemplateRows: "auto 1fr",
        overflow: "hidden",   // no window scrollbars; children manage their own
        background: theme.appBg,
        color: theme.text,
      }}
    >
      {/* Header — top-left, full-bleed dark */}
      <div
        id="app-header"
        style={{
          padding: 16,
          background: theme.headerBg,
          borderBottom: `1px solid ${theme.border}`,
        }}
      >
        <h1 style={{ margin: "0 0 4px 0", color: theme.text }}>pixarr</h1>
        <p className="muted" style={{ margin: 0, color: theme.muted }}>
          staging & review browser. thumbnails for fast scroll; full preview on select.
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          {tabBtn("staging", "Staging")}
          {tabBtn("review", "Review")}
        </div>
      </div>

      {/* Content row (fills remaining height). We hide overflow here and let children scroll internally */}
      <div style={{ minHeight: 0, minWidth: 0, overflow: "hidden" }}>
        {tab === "staging" ? <StagingView theme={theme} /> : <ReviewView />}
      </div>
    </div>
  );
}
