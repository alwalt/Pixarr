// src/App.tsx
import { useState, useMemo } from "react";
import StagingView from "./components/StagingView";
import ReviewView from "./components/ReviewView";
// If you haven't created these yet, keep the stubs below or add real files:
import QuarantineView from "./components/QuarantineView";
import LibraryView from "./components/LibraryView";
import "./App.css";


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

// Layout tokens
const RADIUS = 10;
const BTN_SIZE = 40;
const RAIL_PAD = 8;
const RAIL_BORDER = 1;
const RAIL_EXTRA = 2;
const RAIL_W = BTN_SIZE + RAIL_PAD * 2 + RAIL_BORDER + RAIL_EXTRA;

type Tab = "staging" | "review" | "quarantine" | "library";
type AsideMode = "about" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("staging");

  // Collapsible aside
  const [asideOpen, setAsideOpen] = useState<boolean>(true);
  const [asideMode, setAsideMode] = useState<AsideMode>("about");

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

  const railItems = [
    { mode: "about" as const,    icon: "🛈", label: "About / Instructions" },
    { mode: "settings" as const, icon: "⚙️", label: "Settings" },
  ];

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
    setAsideOpen(false);
  }

  return (
    <div
      id="app-root"
      style={{
        position: "fixed",
        inset: 0,
        display: "grid",
        gridTemplateRows: "auto 1fr",
        gridTemplateColumns: appCols,
        columnGap: 8,
        overflow: "hidden",
        background: theme.appBg,
        color: theme.text,
        paddingBottom: 6,
      }}
    >
      {/* HEADER (left column) */}
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
        <h1 style={{ margin: "0 0 4px 0", color: theme.text, textAlign: "left" }}>Pixarr</h1>
        <p className="muted" style={{ margin: 0, color: theme.muted , textAlign: "left"}}>
          automatic importer for personal photos and videos: watch sources, normalize metadata,
          dedupe, and organize.
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          {tabBtn("staging", "Staging")}
          {tabBtn("review", "Review")}
          {tabBtn("quarantine", "Quarantine")}
          {tabBtn("library", "Library")}
        </div>
      </div>

      {/* MAIN CONTENT (left column) */}
      <div
        style={{
          gridRow: "2 / 3",
          gridColumn: "1 / 2",
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        {tab === "staging" ? (
          <StagingView theme={theme} />
        ) : tab === "review" ? (
          <ReviewView theme={theme} />
        ) : tab === "quarantine" ? (
          <QuarantineView theme={theme} />
        ) : (
          <LibraryView theme={theme} />
        )}
      </div>

      {/* ASIDE (right column spans both rows) */}
      <div
        style={{
          gridRow: "1 / -1",
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
          display: "grid",
          gridTemplateColumns: asideOpen ? `${RAIL_W}px 1fr` : `${RAIL_W}px`,
        }}
      >
        {/* Rail */}
        <div
          role="toolbar"
          aria-label="Aside tools"
          style={{
            width: "100%",
            borderRight: `${RAIL_BORDER}px solid ${theme.border}`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
            padding: RAIL_PAD,
            boxSizing: "border-box",
            background: theme.surface,
          }}
        >
          {railItems.map((item) => {
            const active = asideOpen && asideMode === item.mode;
            return (
              <button
                key={item.mode}
                onClick={() => onRailClick(item.mode)}
                title={item.label}
                aria-label={item.label}
                aria-expanded={active}
                style={{
                  width: BTN_SIZE,
                  height: BTN_SIZE,
                  borderRadius: 10,
                  border: `1px solid ${active ? theme.accentBorder : theme.border}`,
                  background: active ? theme.accent : theme.cardBg,
                  color: "#fff",
                  cursor: "pointer",
                  display: "grid",
                  placeItems: "center",
                  padding: 0,
                  lineHeight: 1,
                  textAlign: "center",
                  fontSize: 20,
                }}
              >
                {item.icon}
              </button>
            );
          })}
        </div>

        {/* Panel */}
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
                {asideMode === "about" ? (
                  <div style={{ color: theme.muted }}><p>About / instructions…</p></div>
                ) : (
                  <div style={{ color: theme.muted }}><p>Settings go here…</p></div>
                )}
              </h2>
            </div>

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
                  <p>Use the icon rail to switch modes.</p>
                  <ul style={{ marginTop: 8 }}>
                    <li>🛈 About — app info.</li>
                    <li>⚙️ Settings — config (coming soon).</li>
                  </ul>
                </>
              ) : (
                <>Settings goes here (toml, coming soon).</>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


