'use client';

import type { RiskView } from '../../lib/useRiskView';
import {
  formatScoreOne,
  formatGateMultiplier,
  gateAsFraction,
  resolveWeights,
  subIndexLabel,
  SUBSTANTIVE_SUBINDEX_KEYS,
} from '../../lib/formatRisk';

interface Props {
  view: RiskView;
  /** Compact for print / nested layouts */
  compact?: boolean;
  /** Overview hero: omit confidence-gate / provisional-weights copy */
  hideGate?: boolean;
}

/** Shared CSS bars — print-safe (no canvas). */
export function SubIndexBars({ view, compact, hideGate }: Props) {
  const weights = resolveWeights(view.weights);
  const gateFrac = hideGate ? null : gateAsFraction(view.gate);
  const raw = view.rawIndex;
  const score = view.score;
  const gatedDiffers =
    !hideGate &&
    raw != null &&
    score != null &&
    Math.abs(raw - score) >= 0.5;

  const keys = SUBSTANTIVE_SUBINDEX_KEYS.filter(
    (k) => view.subIndices[k] != null || weights[k] != null
  );

  if (keys.length === 0 && gateFrac == null) {
    return (
      <p className="text-sm text-stone-500">
        Sub-index scores not available for this assessment.
      </p>
    );
  }

  return (
    <div className={compact ? 'space-y-2' : 'space-y-3'}>
      {keys.map((k) => {
        const v = view.subIndices[k] ?? 0;
        const w = weights[k];
        const pct = Math.max(0, Math.min(100, v));
        return (
          <div key={k}>
            <div className="flex justify-between text-xs mb-1 gap-2">
              <span className="text-stone-600">
                {subIndexLabel(k)}
                {w != null && (
                  <span
                    className="text-stone-400 ml-1"
                    title={`Weight ${w}% of additive index (provisional)`}
                  >
                    ({formatScoreOne(w)}%)
                  </span>
                )}
              </span>
              <span className="text-stone-800 font-mono tabular-nums">
                {formatScoreOne(v)}
              </span>
            </div>
            <div className="h-1.5 bg-[#E8E4DB] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${pct}%`,
                  background: pct > 65 ? '#16a34a' : pct > 40 ? '#d97706' : '#dc2626',
                }}
              />
            </div>
          </div>
        );
      })}

      {gateFrac != null && (
        <div className={compact ? 'pt-1' : 'pt-2 border-t border-[#E4DFD4]'}>
          <div className="flex justify-between text-xs mb-1">
            <span className="text-stone-600">
              Data confidence <span className="text-stone-400">(multiplicative gate)</span>
            </span>
            <span className="text-stone-800 font-mono">
              {formatGateMultiplier(view.gate)}
            </span>
          </div>
          <div className="h-1.5 bg-[#E8E4DB] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-sky-600"
              style={{ width: `${gateFrac * 100}%` }}
            />
          </div>
          {gatedDiffers && (
            <p className="mt-2 text-[11px] text-amber-800 leading-snug">
              Gated {formatGateMultiplier(view.gate)} — raw index {formatScoreOne(raw)} →{' '}
              {formatScoreOne(score)}. Lower confidence usually means cloud gaps or short
              history.
            </p>
          )}
        </div>
      )}

      {!hideGate && (
        <p className="text-[10px] text-stone-400">Weights are provisional (AHP-style).</p>
      )}
    </div>
  );
}
