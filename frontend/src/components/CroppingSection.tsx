import type { AssessmentPayload, CroppingAnalysis } from "../types/assessment";
import { formatNumber } from "../utils/format";

function CropsList({ ca }: { ca: CroppingAnalysis }) {
  const raw = ca.crops_detected;
  if (!raw) return <p style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>No crop list</p>;

  if (Array.isArray(raw)) {
    return (
      <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
        {(raw as string[]).map((c, i) => (
          <li key={i}>{c}</li>
        ))}
      </ul>
    );
  }

  const entries = Object.entries(raw as Record<string, unknown>);
  if (entries.length === 0) return <p className="card-subtitle">No crops detected object</p>;

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Crop</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, val]) => (
            <tr key={name}>
              <td>
                <strong>{name}</strong>
              </td>
              <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem" }}>
                {typeof val === "object" ? JSON.stringify(val) : String(val)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CroppingSection({ data }: { data: AssessmentPayload }) {
  const ca = data.cropping_analysis;
  const stats = data.continuous_data_stats;

  if (!ca && !stats) {
    return (
      <div className="card">
        <h2>Cropping & satellite window</h2>
        <p className="card-subtitle">No cropping analysis in this payload.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Cropping & satellite window</h2>
      {stats && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: "0.75rem",
            marginBottom: "1rem",
          }}
        >
          <Stat label="Grid slots" value={stats.grid_slots} />
          <Stat label="Valid obs." value={stats.valid_observations} />
          <Stat label="Missing" value={stats.missing_observations} />
          <Stat label="Interval (d)" value={stats.interval_days} />
          <Stat label="Span (d)" value={stats.total_days} />
          <Stat
            label="Date range"
            value={
              stats.date_range?.start && stats.date_range?.end
                ? `${stats.date_range.start} → ${stats.date_range.end}`
                : undefined
            }
            raw
          />
        </div>
      )}
      {ca && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
              gap: "0.75rem",
              marginBottom: "1rem",
            }}
          >
            <Stat label="Region" value={ca.region} raw />
            <Stat label="Dominant crop" value={ca.dominant_crop} raw />
            <Stat label="Cropping intensity" value={ca.cropping_intensity} fmt />
            <Stat label="Cultivation signal" value={ca.cultivation_signal} fmt />
            <Stat label="Seasons w/ crops" value={ca.seasons_with_crops} />
            <Stat label="Total seasons" value={ca.total_seasons_analyzed} />
          </div>
          <h3 style={{ fontSize: "0.9rem", margin: "1rem 0 0.5rem" }}>Crops detected</h3>
          <CropsList ca={ca} />
        </>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  fmt,
  raw,
}: {
  label: string;
  value?: unknown;
  fmt?: boolean;
  raw?: boolean;
}) {
  const display =
    value === undefined || value === null
      ? "—"
      : raw
        ? String(value)
        : fmt
          ? formatNumber(value as number, 2)
          : String(value);
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
      <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{display}</div>
    </div>
  );
}
