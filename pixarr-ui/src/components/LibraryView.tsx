import React from "react";

type Theme = {
  surface: string;
  border: string;
  text: string;
  muted: string;
  cardBg: string;
};

export default function LibraryView({ theme }: { theme: Theme }) {
  return (
    <section style={{ height: "100%", padding: 12, color: theme.text }}>
      <div
        style={{
          height: "100%",
          border: `1px dashed ${theme.border}`,
          borderRadius: 10,
          display: "grid",
          placeItems: "center",
          background: theme.surface,
        }}
      >
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 28, marginBottom: 6 }}>📚</div>
          <div>Library — placeholder</div>
          <div style={{ color: theme.muted, fontSize: 12, marginTop: 4 }}>
            Coming soon: finalized assets, search, filters.
          </div>
        </div>
      </div>
    </section>
  );
}
