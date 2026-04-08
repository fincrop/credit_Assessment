import type { AssessmentPayload } from "../types/assessment";
import { formatNumber } from "../utils/format";

export function WeatherSection({ data }: { data: AssessmentPayload }) {
  const wa = data.weather_analysis;
  const intervalBlocks = data.weather_intervals ?? [];
  if (!wa) {
    return (
      <div className="card">
        <h2>Weather & climate stress</h2>
        <p className="card-subtitle">No weather_analysis in payload.</p>
      </div>
    );
  }

  const cycles = wa.cycle_risk_scores ?? [];
  const events = wa.extreme_events ?? [];

  return (
    <div className="card">
      <h2>Weather & climate stress</h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <Mini label="Weather risk score" v={wa.weather_risk_score} />
        <Mini label="Extreme events (total)" v={wa.total_extreme_events} int />
        <Mini label="Critical-stage events" v={wa.critical_stage_events} int />
        <Mini label="Kharif rain (mm)" v={wa.kharif_avg_rainfall_mm} />
        <Mini label="Rabi rain (mm)" v={wa.rabi_avg_rainfall_mm} />
      </div>

      {cycles.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.9rem", margin: "0.5rem 0" }}>Per-cycle weather risk</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Cycle</th>
                  <th>Risk score</th>
                  <th>Events</th>
                </tr>
              </thead>
              <tbody>
                {cycles.map((c, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                      {c.cycle_id ?? `cycle_${i}`}
                    </td>
                    <td>{formatNumber(c.risk_score, 1)}</td>
                    <td>{c.n_events ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {events.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.9rem", margin: "1rem 0 0.5rem" }}>Extreme events (sample)</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Severity</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 25).map((e, i) => (
                  <tr key={i}>
                    <td>{String((e as Record<string, unknown>).type ?? "—")}</td>
                    <td>{String((e as Record<string, unknown>).severity ?? "—")}</td>
                    <td style={{ fontSize: "0.75rem" }}>
                      {String(
                        (e as Record<string, unknown>).date ??
                          (e as Record<string, unknown>).date_or_start ??
                          "—",
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {events.length > 25 && (
              <p style={{ padding: "0.5rem", margin: 0, fontSize: "0.8rem", color: "var(--text-muted)" }}>
                Showing 25 of {events.length} — see raw JSON for full list.
              </p>
            )}
          </div>
        </>
      )}

      {intervalBlocks.length > 0 && (
        <>
          <h3 style={{ fontSize: "0.9rem", margin: "1rem 0 0.5rem" }}>Weather events by crop interval</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Cycle</th>
                  <th>Window</th>
                  <th>Risk</th>
                  <th>Events</th>
                </tr>
              </thead>
              <tbody>
                {intervalBlocks.map((w, i) => (
                  <tr key={i}>
                    <td>{w.cycle_id ?? "-"}</td>
                    <td style={{ fontSize: "0.75rem" }}>
                      {w.start_date ?? "?"} → {w.end_date ?? "?"}
                    </td>
                    <td>{formatNumber(w.weather_risk, 1)}</td>
                    <td>{w.event_count ?? w.events?.length ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Mini({ label, v, int }: { label: string; v?: number; int?: boolean }) {
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
