export function Header() {
  return (
    <header className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
        <span style={{ fontSize: "1.75rem" }} aria-hidden>
          🌾
        </span>
        <div>
          <h1 style={{ margin: 0, fontSize: "1.35rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
            Satellite Credit Risk Assessment
          </h1>
          <p style={{ margin: "0.15rem 0 0", fontSize: "0.85rem", color: "var(--text-muted)" }}>
            Field intelligence · NDVI cycles · Weather · Credit scoring · Explainability
          </p>
        </div>
      </div>
    </header>
  );
}
