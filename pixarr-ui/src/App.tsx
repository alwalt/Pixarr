import { useState } from "react";
import ReviewView from "./components/ReviewView";
import StagingView from "./components/StagingView";

type Tab = "staging" | "review";

export default function App() {
  const [tab, setTab] = useState<Tab>("staging");

  const tabBtn = (t: Tab, label: string) => (
    <button
      onClick={() => setTab(t)}
      style={{
        padding: "6px 14px",
        borderRadius: 6,
        border: "1px solid #888",
        background: tab === t ? "#2563eb" : "#f9fafb",
        color: tab === t ? "white" : "#111",
        fontWeight: tab === t ? 600 : 400,
        cursor: "pointer",
        minWidth: 90,
      }}
    >
      {label}
    </button>
  );

  return (
    <div className="app-shell" style={{ maxWidth: 1280, margin: "0 auto", padding: 16 }}>
      <h1 style={{ marginBottom: 4 }}>pixarr</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        staging & review browser. thumbnails for fast scroll; full preview on select.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {tabBtn("staging", "Staging")}
        {tabBtn("review", "Review")}
      </div>

      {tab === "staging" ? <StagingView /> : <ReviewView />}
    </div>
  );
}
