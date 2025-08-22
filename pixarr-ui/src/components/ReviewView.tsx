import React from "react";

type Theme = {
  surface: string;
  border: string;
  text: string;
  muted: string;
  cardBg: string;
  accent: string;
  accentBorder: string;
};

export default function ReviewView({ theme }: { theme: Theme }) {
  return (
    <section style={{ height: "100%", padding: 12, color: theme.text }}>
      <div
        style={{
          height: "100%",
          display: "grid",
          gridTemplateRows: "auto 1fr",
          gap: 12,
        }}
      >
        {/* Toolbar (placeholder) */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            style={{
              padding: "6px 10px",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
              background: theme.cardBg,
              color: theme.text,
              cursor: "pointer",
            }}
          >
            Approve → Library
          </button>
          <button
            style={{
              padding: "6px 10px",
              borderRadius: 8,
              border: `1px solid ${theme.border}`,
              background: theme.cardBg,
              color: theme.text,
              cursor: "pointer",
            }}
          >
            Send to Quarantine
          </button>
          <div style={{ marginLeft: "auto", color: theme.muted, fontSize: 12 }}>
            Review — placeholder
          </div>
        </div>

        {/* Body: left grid area + right preview slot (placeholders) */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr minmax(320px, 40%)",
            gap: 12,
            minHeight: 0,
          }}
        >
          {/* Left: items/list placeholder */}
          <div
            style={{
              height: "100%",
              border: `1px dashed ${theme.border}`,
              borderRadius: 10,
              display: "grid",
              placeItems: "center",
              background: theme.surface,
              textAlign: "center",
              padding: 16,
            }}
          >
            <div>
              <div style={{ fontSize: 28, marginBottom: 6 }}>🧾</div>
              <div>Review queue — placeholder</div>
              <div style={{ color: theme.muted, fontSize: 12, marginTop: 4 }}>
                Coming soon: grid of items pending approval.
              </div>
            </div>
          </div>

          {/* Right: preview/metadata placeholder */}
          <div
            style={{
              height: "100%",
              border: `1px dashed ${theme.border}`,
              borderRadius: 10,
              display: "grid",
              placeItems: "center",
              background: theme.surface,
              textAlign: "center",
              padding: 16,
            }}
          >
            <div>
              <div style={{ fontSize: 28, marginBottom: 6 }}>🖼️</div>
              <div>Preview — placeholder</div>
              <div style={{ color: theme.muted, fontSize: 12, marginTop: 4 }}>
                Coming soon: full preview, EXIF, per-file edits.
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
