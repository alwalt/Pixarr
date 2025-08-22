import { useState, useMemo } from "react";
import ReviewView from "./components/ReviewView";
import StagingView from "./components/StagingView";

/** Dark theme tokens */
const theme = {
  appBg: "#0b0c0f",
  headerBg: "#0f1115",
  surface: "#111318",
  cardBg: "#12151b",
  border: "#23262e",
  text: "#e5e7eb",
  muted: "#9ca3af",
  accent: "#2563eb",
  accentBorder: "#3b82f6",
};

// // Keep a single radius value so header + aside stay consistent
const RADIUS = 10;
const BTN_SIZE = 40;
const RAIL_PAD = 8;
const RAIL_BORDER = 1;
const RAIL_EXTRA = 2; // small breathing room so it never feels cramped
const RAIL_W = BTN_SIZE + RAIL_PAD * 2 + RAIL_BORDER + RAIL_EXTRA;

type Tab = "staging" | "review";
type AsideMode = "about" | "preview"; // extend later as you add more functions

export default function App() {
  const [tab, setTab] = useState<Tab>("staging");

  // ⬇️ Collapsible aside: when collapsed, right column is a thin rail (single icon).
  const [asideOpen, setAsideOpen] = useState<boolean>(true);
  const [asideMode, setAsideMode] = useState<AsideMode>("about");

  // App grid columns depend on collapse state:
  // - Expanded: left 3fr, right 1fr
  // - Collapsed: left 1fr, right RAIL_W
  const appCols = useMemo(
    () => (asideOpen ? "3fr 1fr" : `1fr ${RAIL_W}px`),
    [asideOpen]
  );

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

  // Icon rail items — using emoji (“🛈”) style
  const railItems: Array<{ mode: AsideMode; icon: string; label: string }> = [
    { mode: "about", icon: "🛈", label: "About / Instructions" },
    { mode: "preview", icon: "🖼️", label: "Preview" },
  ];

  // const toggleAside = () => setAsideOpen((v) => !v);
  // Clicking an icon:
  // - If collapsed → open and set that mode
  // - If expanded + different mode → switch mode
  // - If expanded + same mode → collapse
  function onRailClick(next: AsideMode) {
    if (!asideOpen) {
      setAsideMode(next);
      setAsideOpen(true);
      return;
    }
    if (asideMode !== next) {
      setAsideMode(next);
      return;
    }
    // same mode while open -> collapse
    setAsideOpen(false);
  }

  return (
    // Viewport grid:
    // Rows: [header (auto), content (1fr)]
    // Cols: [main (3fr), aside (1fr)]
    <div
      id="app-root"
      style={{
        position: "fixed",
        inset: 0,
        display: "grid",
        gridTemplateRows: "auto 1fr",
        gridTemplateColumns: appCols,
        columnGap: 8,           // visual gutter between main and aside
        overflow: "hidden",
        background: theme.appBg,
        color: theme.text,
      }}
    >
      {/* HEADER — lives only in LEFT column, Row 1 */}
      {/* This ensures the aside can sit next to it in the RIGHT column from the very top. */}
      <div
        id="app-header"
        style={{
          gridRow: "1 / 2",
          gridColumn: "1 / 2",
          padding: 16,
          background: theme.headerBg,
          borderBottom: `1px solid ${theme.border}`,
          borderRight: `1px solid ${theme.border}`,
          overflow: "hidden",
          borderTopRightRadius: RADIUS,
          borderBottomRightRadius: RADIUS,
        }}
      >
        <h1 style={{ margin: "0 0 4px 0", color: theme.text }}>pixarr</h1>
        <p className="muted" style={{ margin: 0, color: theme.muted }}>
          automatic importer for personal photos and videos: watch sources, normalize metadata, dedupe, and organize.
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          {tabBtn("staging", "Staging")}
          {tabBtn("review", "Review")}
        </div>
      </div>

      {/* MAIN CONTENT — LEFT column, Row 2 */}
      <div
        style={{
          gridRow: "2 / 3",
          gridColumn: "1 / 2",
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        {tab === "staging" ? <StagingView theme={theme} /> : <ReviewView />}
      </div>

      {/* ASIDE — right column spans BOTH rows (continuous from top to bottom). 
          Inside the aside, the FIRST column is a fixed-width icon rail (RAIL_W),
          which stays in the SAME position whether collapsed or expanded.
          When collapsed: aside column is exactly RAIL_W wide → only rail visible.
          When expanded: aside column grows → rail (RAIL_W) + panel (1fr). */}
      <div
        style={{
          gridRow: "1 / -1",      // 👈 span header + content
          gridColumn: "2 / 3",
          minHeight: 0,
          minWidth: 0,
          background: theme.surface,
          borderLeft: `1px solid ${theme.border}`,
          padding: 0,
          boxSizing: "border-box",
          borderTopLeftRadius: RADIUS,
          borderBottomLeftRadius: RADIUS,
          color: theme.text,
          overflow: "hidden",
          // borderRadius: 10,        // optional; remove if you want a perfectly flush column
          display: "grid",
          gridTemplateColumns: asideOpen ? `${RAIL_W}px 1fr` : `${RAIL_W}px`, // rail + panel'
        }}
      >
        {/* -------- Icon Rail (fixed width, always visible, same position) -------- */}
        <div
          style={{
            width: "100%",
            // borderRight: `1px solid ${theme.border}`,
            borderRight: `${RAIL_BORDER}px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
            padding: RAIL_PAD,
            boxSizing: "border-box",
            background: theme.surface,
          }}
          role="toolbar"
          aria-label="Aside tools"
        >
          {railItems.map((item) => {
            const active = asideOpen && asideMode === item.mode;
            const expanded = active ? true : false;
            return (
              <button
                key={item.mode}
                onClick={() => onRailClick(item.mode)}
                title={item.label}
                aria-label={item.label}
                aria-expanded={expanded}
                style={{
                  width: BTN_SIZE,
                  height: BTN_SIZE,
                  borderRadius: 10,
                  border: `1px solid ${active ? theme.accentBorder : theme.border}`,
                  background: active ? theme.accent : theme.cardBg,
                  color: "#fff",
                  cursor: "pointer",
                  // 👇 center the emoji perfectly
                  display: "grid",
                  placeItems: "center",
                  padding: 0,
                  lineHeight: 1,
                  textAlign: "center",
                  // optional: make emoji size consistent
                  fontSize: 20,
                }}
              >
                {item.icon}
              </button>
            );
          })}
        </div>
        {/* -------- Panel (only rendered when expanded) -------- */}
        {asideOpen && (
          <div
            id="aside-panel"
            role="region"
            aria-labelledby="aside-title"
            style={{
              minHeight: 0,
              minWidth: 0,
              display: "grid",
              gridTemplateRows: "auto 1fr",
              gap: 8,
              padding: 12,
              boxSizing: "border-box",
              overflow: "hidden",
            }}
          >
            {/* Panel header (shows active section title) */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                borderBottom: `1px solid ${theme.border}`,
                paddingBottom: 8,
              }}
            >
              <h2 style={{ margin: 0, fontSize: 16, flex: 1 }}>
                {asideMode === "about" ? "About / Instructions" : "Preview"}
              </h2>
            </div>

            {/* Panel body (scrollable) — placeholder for now */}
            <div
              style={{
                overflow: "auto",
                minHeight: 0,
                color: theme.muted,
                lineHeight: 1.6,
              }}
            >
              {asideMode === "about" ? (
                <>
                  <p>
                    This panel will show instructions and app info. Use the icon rail to switch modes.
                  </p>
                  <ul style={{ marginTop: 8 }}>
                    <li>🛈 About — read instructions and app details.</li>
                    <li>🖼️ Preview — view the selected image with EXIF (coming soon).</li>
                  </ul>
                </>
              ) : (
                <>Preview goes here (image + EXIF, coming soon).</>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}





