import type { AssessmentPayload } from "../types/assessment";
import { formatNumber, riskBadgeClass } from "../utils/format";

export function SummaryHero({ data }: { data: AssessmentPayload }) {
  const ca = data.credit_assessment;
  const cr = data.credit_recommendations;
  const score = ca?.credit_score ?? data.summary?.credit_score;
  const risk = ca?.risk_category ?? data.summary?.risk_category;
  const pct = typeof score === "number" ? Math.min(100, Math.max(0, score)) : 0;

  const limit =
    cr?.recommended_limit ??
    cr?.recommended_credit_limit ??
    (data.summary?.credit_limit as number | undefined);

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
            <strong>Recommended limit:</strong>{" "}
            {limit != null ? <>₹{formatNumber(limit, 0)}</> : "—"}
            {cr?.limit_per_hectare != null && (
              <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
                {" "}
                (~₹{formatNumber(cr.limit_per_hectare, 0)} / ha)
              </span>
            )}
          </p>
          {cr?.interest_rate != null && (
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.85rem", color: "var(--text-muted)" }}>
              Interest ~{formatNumber(cr.interest_rate, 2)}% ·{" "}
              {cr.repayment_period_months ?? cr.repayment_months ?? "—"} months · Collateral:{" "}
              {cr.collateral_required ? "Yes" : "No"}
            </p>
          )}
          {data.processing_time_seconds != null && (
            <p style={{ margin: "0.5rem 0 0", fontSize: "0.8rem", color: "var(--text-muted)" }}>
              Processing {formatNumber(data.processing_time_seconds, 1)}s · Pipeline stages:{" "}
              {(data.pipeline_stages ?? []).length}
            </p>
          )}
        </div>
      </div>
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
      {Array.isArray(cr?.conditions) && cr!.conditions!.length > 0 && (
        <ul style={{ margin: "0.75rem 0 0", paddingLeft: "1.15rem", fontSize: "0.85rem" }}>
          {cr!.conditions!.map((c, i) => (
            <li key={i}>{c}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
