import type { AssessmentPayload } from "../types/assessment";
import { formatNumber } from "../utils/format";

export function CropCyclesSection({ data }: { data: AssessmentPayload }) {
  const cc = data.crop_cycles;
  if (!cc) {
    return (
      <div className="card">
        <h2>Detected crop cycles</h2>
        <p className="card-subtitle">No crop_cycles block in payload.</p>
      </div>
    );
  }

  const um = cc.utilization_metrics ?? {};
  const cycles = cc.cycles ?? [];

  return (
    <div className="card">
      <h2>Land use & crop cycles</h2>
      <div style={{ marginBottom: "1rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        <span className="tag">{cc.detected ? "Cycles detected" : "No cycles flag"}</span>
        <span className="tag">Count: {cc.cycles_count ?? cycles.length}</span>
        {cc.method && <span className="tag">{cc.method}</span>}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <Mini label="Land utilization index" v={um.land_utilization_index} pct />
        <Mini label="Crops / year" v={um.crops_per_year} />
        <Mini label="Pattern" v={um.cropping_pattern} text />
      </div>

      {cycles.length > 0 && (
        <details>
          <summary>Raw cycle objects ({cycles.length})</summary>
          <pre className="json-pre" style={{ marginTop: "0.75rem" }}>
            {JSON.stringify(cycles, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function Mini({
  label,
  v,
  pct,
  text,
}: {
  label: string;
  v?: unknown;
  pct?: boolean;
  text?: boolean;
}) {
  const display =
    v === undefined || v === null
      ? "—"
      : text
        ? String(v)
        : pct
          ? (() => {
              const n = Number(v);
              if (Number.isNaN(n)) return "—";
              return n <= 1 ? `${formatNumber(n * 100, 1)}%` : formatNumber(n, 1);
            })()
          : formatNumber(Number(v), 2);
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        padding: "0.5rem 0.65rem",
        borderRadius: 8,
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontWeight: 600 }}>{display}</div>
    </div>
  );
}
