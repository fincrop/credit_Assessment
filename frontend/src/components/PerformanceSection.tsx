import type { AssessmentPayload } from "../types/assessment";
import { formatNumber, formatPct } from "../utils/format";

export function PerformanceSection({ data }: { data: AssessmentPayload }) {
  const pa = data.performance_analysis;
  if (!pa) {
    return (
      <div className="card">
        <h2>Performance</h2>
        <p className="card-subtitle">No performance_analysis in payload.</p>
      </div>
    );
  }

  const rows = pa.seasonal_performance ?? [];

  return (
    <div className="card">
      <h2>Crop performance (per cycle / season)</h2>
      <div
      style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <Avg label="Avg health" v={pa.average_health_score} />
        <Avg label="Avg yield score" v={pa.average_yield_score} />
        <Avg label="Avg performance" v={pa.average_performance_score} />
        <Avg label="Seasons scored" v={pa.n_seasons_analyzed} int />
        <Avg label="Complete cycles" v={pa.n_complete_cycles} int />
        <Avg label="Active cycles" v={pa.n_active_cycles} int />
      </div>

      {rows.length === 0 ? (
        <p className="card-subtitle">No seasonal_performance rows.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Season</th>
                <th>Year</th>
                <th>Crop</th>
                <th>Health</th>
                <th>Yield</th>
                <th>Method</th>
                <th>Anomalies</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const an = r.anomaly_events ?? [];
                const nHigh = an.filter((e) => e.impact === "HIGH").length;
                return (
                  <tr key={i}>
                    <td>{(r.season ?? "—").toString().toUpperCase()}</td>
                    <td>{r.year ?? "—"}</td>
                    <td>{r.crop ?? "—"}</td>
                    <td>{formatNumber(r.health_score, 1)}</td>
                    <td>{formatPct(r.yield_potential_pct)}</td>
                    <td>
                      <span className="tag">{r.scoring_method ?? "—"}</span>
                      {r.is_active_cycle && (
                        <span className="tag" style={{ marginLeft: 4 }}>
                          active
                        </span>
                      )}
                    </td>
                    <td>
                      {an.length}
                      {nHigh > 0 && (
                        <span className="tag tag-high" style={{ marginLeft: 4 }}>
                          {nHigh} high
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.some((r) => (r.performance_narrative ?? "").length > 0) && (
        <details style={{ marginTop: "1rem" }}>
          <summary>Per-cycle narratives</summary>
          <ul style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
            {rows.map((r, i) =>
              r.performance_narrative ? (
                <li key={i} style={{ marginBottom: "0.5rem" }}>
                  <strong>
                    {r.season} {r.year}
                  </strong>
                  : {r.performance_narrative}
                </li>
              ) : null,
            )}
          </ul>
        </details>
      )}
    </div>
  );
}

function Avg({ label, v, int }: { label: string; v?: number; int?: boolean }) {
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
      <div style={{ fontWeight: 600 }}>{v != null ? (int ? String(v) : formatNumber(v, 1)) : "—"}</div>
    </div>
  );
}
