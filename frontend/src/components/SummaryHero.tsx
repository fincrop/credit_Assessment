import type { AssessmentPayload } from "../types/assessment";
import { formatNumber, riskBadgeClass } from "../utils/format";

export function SummaryHero({ data }: { data: AssessmentPayload }) {
  const ca = data.credit_assessment;
  const score = ca?.credit_score ?? data.summary?.credit_score;
  const risk = ca?.risk_category ?? data.summary?.risk_category;
  const pct = typeof score === "number" ? Math.min(100, Math.max(0, score)) : 0;

  const method =
    ca?.method ?? (typeof data.summary?.scoring_method === "string" ? data.summary.scoring_method : undefined);
  const narrative = ca?.scoring_narrative;
  const riskLabel = typeof risk === "string" ? risk : "—";

  return (
    <div className="card">
      <h2>Credit outcome</h2>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "1.5rem",
          alignItems: "center",
        }}
      >
        <div className="score-ring" style={{ ["--pct" as string]: String(pct) }}>
          <div className="score-ring-inner">
            <strong>{formatNumber(score, 1)}</strong>
            <span>Score</span>
          </div>
        </div>
        <div style={{ flex: "1", minWidth: "220px" }}>
          <div style={{ marginBottom: "0.5rem" }}>
            <span className={`risk-chip ${riskBadgeClass(typeof risk === "string" ? risk : undefined)}`}>
              {riskLabel}
            </span>
            {method ? (
              <span className="tag" style={{ marginLeft: "0.5rem" }}>
                {method}
              </span>
            ) : null}
            {ca?.ml_components_silenced && (
              <span className="tag" style={{ marginLeft: "0.35rem" }} title="ML blend disabled">
                ML silenced
              </span>
            )}
          </div>
          <p style={{ margin: 0, fontSize: "0.95rem" }}>
            <strong>Assessment quality:</strong> {score != null ? formatNumber(score, 1) : "—"} / 100
          </p>
          {data.processing_time_seconds != null && (
            <p style={{ margin: "0.5rem 0 0", fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Processing {formatNumber(data.processing_time_seconds, 1)}s · Pipeline stages:{" "}
              {(data.pipeline_stages ?? []).length}
            </p>
          )}
        </div>
      </div>
      {ca?.component_scores && (
        <div className="grid-2" style={{ marginTop: "1rem" }}>
          {Object.entries(ca.component_scores).map(([k, v]) => (
            <div key={k} className="bar-row">
              <div className="bar-label">
                <span>{k.replaceAll("_", " ")}</span>
                <span>{formatNumber(v, 1)}</span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}
      {narrative && (
        <p
          style={{
            marginTop: "1rem",
            paddingTop: "1rem",
            borderTop: "1px solid var(--border)",
            fontSize: "0.88rem",
            color: "var(--text-muted)",
          }}
        >
          {narrative}
        </p>
      )}
    </div>
  );
}
