// src/App.tsx
import { useState, useMemo, useEffect, useCallback } from "react";
import StagingView from "./components/StagingView";
import ReviewView from "./components/ReviewView";
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

/**
 * Persist helpers — safe sessionStorage with lint- and target-friendly catch blocks.
 * No empty catches (eslint no-empty) and no unused vars.
 */
const ss = {
  getString(key: string): string | null {
    try {
      return typeof window !== "undefined" ? window.sessionStorage.getItem(key) : null;
    } catch (e) {
      void e; // storage may be unavailable (private mode/quota/SSR)
      return null;
    }
  },
  getJSON<T>(key: string): T | null {
    try {
      const raw = typeof window !== "undefined" ? window.sessionStorage.getItem(key) : null;
      return raw ? (JSON.parse(raw) as T) : null;
    } catch (e) {
      void e; // invalid JSON or storage unavailable
      return null;
    }
  },
  set(key: string, value: unknown): void {
    try {
      if (typeof window === "undefined") return;
      const v = typeof value === "string" ? value : JSON.stringify(value);
      window.sessionStorage.setItem(key, v);
    } catch (e) {
      void e; // ignore persist errors; non-critical
    }
  },
};

/** What we remember for Staging so your place is preserved across tab switches. */
export type StagingSavedState = {
  root: string;
  path: string;
  selectedRel?: string;
  scrollTop?: number;
};

const defaultStaging: StagingSavedState = {
  root: "",
  path: "",
  selectedRel: undefined,
  scrollTop: 0,
};

/** Shallow compare to avoid no-op state writes (prevents unnecessary rerenders). */
function shallowEqualStaging(a: StagingSavedState, b: StagingSavedState): boolean {
  return (
    a.root === b.root &&
    a.path === b.path &&
    a.selectedRel === b.selectedRel &&
    (a.scrollTop ?? 0) === (b.scrollTop ?? 0)
  );
}

export default function App() {
  // Persist active tab so returning users land where they left off
  const [tab, setTab] = useState<Tab>(() => {
    const saved = ss.getString("pixarr.tab") as Tab | null;
    return saved ?? "staging";
  });

  // Collapsible aside (persist open state + mode) — with safe JSON parse
  const [asideOpen, setAsideOpen] = useState<boolean>(() => {
    const saved = ss.getString("pixarr.asideOpen");
    if (!saved) return true;
    try {
      return JSON.parse(saved) as boolean;
    } catch {
      return true;
    }
  });
  const [asideMode, setAsideMode] = useState<AsideMode>(() => {
    const saved = ss.getString("pixarr.asideMode") as AsideMode | null;
    return saved ?? "about";
  });

  // Grid column layout reacts to aside open/closed
  const appCols = useMemo(() => (asideOpen ? "3fr 1fr" : `1fr ${RAIL_W}px`), [asideOpen]);

  // Lifted Staging state so it survives unmounts when you switch tabs
  const [stagingState, setStagingState] = useState<StagingSavedState>(() => {
    return ss.getJSON<StagingSavedState>("pixarr.staging") ?? defaultStaging;
  });

  /** Stable callback to receive state updates from StagingView. */
  const onStagingStateChange = useCallback((next: StagingSavedState) => {
    setStagingState((prev) => {
      if (prev && shallowEqualStaging(prev, next)) return prev; // no change → no rerender
      ss.set("pixarr.staging", next);
      return next;
    });
  }, []);

  // Persist small UI bits
  useEffect(() => {
    ss.set("pixarr.tab", tab);
  }, [tab]);

  useEffect(() => {
    ss.set("pixarr.asideOpen", JSON.stringify(asideOpen));
    ss.set("pixarr.asideMode", asideMode);
  }, [asideOpen, asideMode]);

  /** Tab button factory (kept inline for brevity; can be converted to classes later) */
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
    { mode: "about" as const, icon: "🛈", label: "About / Instructions" },
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

        // Publish theme as CSS variables for child class-based styles
        ["--bg-app" as any]: theme.appBg,
        ["--bg-header" as any]: theme.headerBg,
        ["--bg-surface" as any]: theme.surface,
        ["--bg-card" as any]: theme.cardBg,
        ["--border" as any]: theme.border,
        ["--text" as any]: theme.text,
        ["--muted" as any]: theme.muted,
        ["--accent" as any]: theme.accent,
        ["--accent-border" as any]: theme.accentBorder,
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
        <p className="muted" style={{ margin: 0, color: theme.muted, textAlign: "left" }}>
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
          <StagingView
            theme={theme}
            savedState={stagingState}                 // restore root/path/selection/scroll
            onSavedStateChange={onStagingStateChange} // keep state in sync as user navigates
          />
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
              {/* Accessible heading id matches aria-labelledby */}
              <h2 id="aside-title" style={{ margin: 0, fontSize: 16, flex: 1, color: theme.text }}>
                {asideMode === "about" ? "About / instructions" : "Settings"}
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
