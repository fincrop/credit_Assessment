import type { AssessmentPayload } from "../types/assessment";
import { formatNumber, humanizeKey } from "../utils/format";

export function AIEnrichmentSection({ data }: { data: AssessmentPayload }) {
  const ai = data.ai_enrichment;
  if (!ai) {
    return (
      <div className="card">
        <h2>AI & explainability</h2>
        <p className="card-subtitle">No ai_enrichment in this run (optional Stage 12).</p>
      </div>
    );
  }

  const ex = ai.explainability ?? ai.explainability_mongo;
  const cf = (ai.counterfactuals_mongo ?? ai.counterfactuals) as
    | {
        current_score?: number;
        projected_score_all_improvements?: number;
        scenarios?: { id?: string; title?: string; score_gain?: number; component?: string; feasibility?: string }[];
        improvement_roadmap?: string;
      }
    | undefined;

  return (
    <div className="card">
      <h2>AI & explainability</h2>
      <p className="card-subtitle">
        Groq: {ai.groq_used ? "used" : "skipped"}
        {ai.groq_skipped_reason ? ` — ${ai.groq_skipped_reason}` : ""}
        {ai.translation_language && ` · Translation: ${ai.translation_language}`}
      </p>

      {(ai.english_preview || ai.english_narrative) && (
        <div style={{ marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "0.85rem", margin: "0 0 0.35rem" }}>Narrative (EN)</h3>
          <p style={{ fontSize: "0.88rem", color: "var(--text-muted)", margin: 0 }}>
            {ai.english_preview ?? ai.english_narrative}
          </p>
        </div>
      )}
      {(ai.translated_preview || ai.translated_narrative) && (
        <div style={{ marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "0.85rem", margin: "0 0 0.35rem" }}>Narrative (translated)</h3>
          <p style={{ fontSize: "0.88rem", color: "var(--text-muted)", margin: 0 }}>
            {ai.translated_preview ?? ai.translated_narrative}
          </p>
        </div>
      )}

      {ex && (
        <>
          <h3 style={{ fontSize: "0.9rem", margin: "0 0 0.5rem" }}>
            Drivers ({ex.method ?? "attribution"})
          </h3>
          {ex.credit_summary && (
            <p style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>{ex.credit_summary}</p>
          )}
          <div className="grid-2" style={{ marginTop: "0.75rem" }}>
            <DriverList title="Positive drivers" items={ex.top_positive_drivers ?? []} sign="+" />
            <DriverList title="Negative drivers" items={ex.top_negative_drivers ?? []} sign="−" />
          </div>
        </>
      )}

      {cf && (cf.scenarios?.length ?? 0) > 0 && (
        <>
          <h3 style={{ fontSize: "0.9rem", margin: "1.25rem 0 0.5rem" }}>Counterfactual scenarios</h3>
          <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
            Current score {formatNumber(cf.current_score, 1)} → projected with improvements{" "}
            {formatNumber(cf.projected_score_all_improvements, 1)} (heuristic ceiling).
          </p>
          <ul style={{ paddingLeft: "1.1rem", margin: "0.5rem 0 0" }}>
            {(cf.scenarios ?? []).map((s, i) => (
              <li key={s.id ?? i} style={{ marginBottom: "0.5rem", fontSize: "0.88rem" }}>
                <strong>{s.title}</strong>
                {s.score_gain != null && (
                  <span style={{ color: "var(--accent)", marginLeft: "0.35rem" }}>
                    +{formatNumber(s.score_gain, 1)} pts
                  </span>
                )}
                {s.component && (
                  <span className="tag" style={{ marginLeft: "0.35rem" }}>
                    {s.component}
                  </span>
                )}
                {s.feasibility && (
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginLeft: "0.35rem" }}>
                    ({s.feasibility})
                  </span>
                )}
              </li>
            ))}
          </ul>
          {cf.improvement_roadmap && (
            <p style={{ fontSize: "0.82rem", marginTop: "0.75rem", color: "var(--text-muted)" }}>
              {cf.improvement_roadmap}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function DriverList({
  title,
  items,
  sign,
}: {
  title: string;
  items: { feature?: string; label?: string; contribution?: number; value?: number }[];
  sign: string;
}) {
  return (
    <div>
      <h4 style={{ fontSize: "0.78rem", color: "var(--text-muted)", margin: "0 0 0.35rem" }}>{title}</h4>
      <ul style={{ margin: 0, paddingLeft: "1rem", fontSize: "0.82rem" }}>
        {items.length === 0 && <li>—</li>}
        {items.map((d, i) => (
          <li key={i}>
            {d.label ?? humanizeKey(d.feature ?? "")}{" "}
            {d.contribution != null && (
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                {sign}
                {formatNumber(Math.abs(d.contribution), 3)}
              </span>
            )}
            {d.value != null && (
              <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
                {" "}
                (value {formatNumber(d.value, 1)})
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
