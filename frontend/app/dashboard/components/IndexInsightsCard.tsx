'use client';

import type { RiskView } from '../../lib/useRiskView';
import { formatScoreOne, polarityIcon, polarityBgClass } from '../../lib/formatRisk';
import type { FarmAssessment } from '../../types/assessment';

/**
 * Reason codes for a plot or holding.
 *
 * The KBS / raw / gate figures that used to head this card now live in
 * ScoreWaterfall, where they are shown as the arithmetic that produced them
 * rather than three disconnected tiles. Keeping a second copy here would mean
 * two components restating the same numbers — which is how they end up
 * disagreeing, exactly as the four colour ramps did.
 */
export function IndexInsightsCard({
  view,
  scopeLabel = 'Plot',
}: {
  view: RiskView;
  scopeLabel?: string;
}) {
  const codes = (view.reasonCodes || []).slice(0, 8);

  return (
    <div className="bg-white rounded-xl border border-rule p-5 space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-1">
          Findings
        </h2>
        <p className="text-xs text-stone-500 leading-relaxed">
          Structured observations recorded for this {scopeLabel.toLowerCase()} during scoring.
        </p>
      </div>

      <div>
        <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-2">
          Reason codes
        </p>
        {codes.length === 0 ? (
          <p className="text-sm text-stone-500">No structured reason codes for this plot.</p>
        ) : (
          <ul className="space-y-2">
            {codes.map((rc, i) => (
              <li
                key={`${rc.code ?? i}-${i}`}
                className={`flex gap-2 text-xs rounded-lg border px-3 py-2 ${polarityBgClass(rc.polarity)}`}
              >
                <span className="font-mono shrink-0" aria-hidden>
                  {polarityIcon(rc.polarity)}
                </span>
                <span>
                  {rc.code && (
                    <span className="font-mono text-[10px] opacity-70 mr-1.5">{rc.code}</span>
                  )}
                  {rc.message || rc.code || '—'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** When plot detail is missing (older slim payloads), show what we still know. */
export function FarmSlimFallbackCard({ farm }: { farm: FarmAssessment }) {
  const seasons = farm.season_types || [];
  return (
    <div className="bg-white rounded-xl border border-rule p-5 space-y-3">
      <h3 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
        Plot snapshot
      </h3>
      <p className="text-xs text-stone-500 leading-relaxed">
        Full crop / weather / cycle panels need a fresh assessment (plot detail is stored on new
        runs). Showing slim fields from this job:
      </p>
      <div className="grid sm:grid-cols-3 gap-2">
        <div className="rounded-lg border border-rule bg-paper/50 p-3">
          <p className="text-[10px] text-ink-muted uppercase font-semibold">Crop</p>
          <p className="text-sm font-semibold text-stone-800 mt-0.5">{farm.crop || '—'}</p>
        </div>
        <div className="rounded-lg border border-rule bg-paper/50 p-3">
          <p className="text-[10px] text-ink-muted uppercase font-semibold">Area</p>
          <p className="text-sm font-semibold text-stone-800 mt-0.5 font-mono">
            {farm.area_ha != null ? `${Number(farm.area_ha).toFixed(2)} ha` : '—'}
          </p>
        </div>
        <div className="rounded-lg border border-rule bg-paper/50 p-3">
          <p className="text-[10px] text-ink-muted uppercase font-semibold">Tenure</p>
          <p className="text-sm font-semibold text-stone-800 mt-0.5 font-mono">
            {formatScoreOne(farm.tenure_factor)}
          </p>
        </div>
      </div>
      {seasons.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {seasons.map((s, i) => (
            <span
              key={`${String(s)}-${i}`}
              className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-800"
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
