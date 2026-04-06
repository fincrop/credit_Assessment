import type { CreditAssessment } from "../types/assessment";
import { formatNumber, humanizeKey } from "../utils/format";

const DEFAULT_WEIGHTS: Record<string, number> = {
  crop_detection: 35,
  crop_performance: 25,
  yield_potential: 15,
  weather_safety: 8,
  anomaly_penalty: 7,
  cropping_intensity: 5,
  govt_benefits: 5,
};

export function ComponentScores({ credit }: { credit: CreditAssessment | undefined }) {
  const scores = credit?.component_scores ?? {};
  const weights = credit?.component_weights ?? DEFAULT_WEIGHTS;
  const weak = new Set(credit?.weak_components ?? []);

  const keys = Object.keys({ ...DEFAULT_WEIGHTS, ...scores });

  return (
    <div className="card">
      <h2>Score breakdown</h2>
      <p className="card-subtitle">
        Weighted components (rule-based v4). Bars show contribution quality (0–100) per factor.
      </p>
      {keys.map((k) => {
        const v = scores[k] ?? 0;
        const w = weights[k] ?? DEFAULT_WEIGHTS[k] ?? 0;
        const weighted = (v * w) / 100;
        return (
          <div key={k} className="bar-row">
            <div className="bar-label">
              <span>
                {humanizeKey(k)}
                {weak.has(k) && (
                  <span className="tag tag-med" style={{ marginLeft: "0.35rem" }}>
                    weak
                  </span>
                )}
              </span>
              <span>
                {formatNumber(v, 1)} / 100 × {w}% → {formatNumber(weighted, 2)} pts
              </span>
            </div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${Math.min(100, v)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
