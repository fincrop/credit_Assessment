'use client';

import type { RiskView } from '../../lib/useRiskView';
import {
  formatScoreOne,
  formatGateMultiplier,
  polarityIcon,
  polarityBgClass,
  subIndexLabel,
} from '../../lib/formatRisk';
import { toKbsScore, kbsBandForScore, KBS_MAX } from '../../lib/kbsScore';
import type { FarmAssessment } from '../../types/assessment';

/** Overview tab — KBS metrics + reason codes for a plot or holding. */
export function IndexInsightsCard({
  view,
  scopeLabel = 'Plot',
}: {
  view: RiskView;
  scopeLabel?: string;
}) {
  const codes = (view.reasonCodes || []).slice(0, 8);
  const kbs = toKbsScore(view.score);
  const band = kbsBandForScore(kbs);
  const rawKbs = toKbsScore(view.rawIndex);

  return (
    <div className="bg-white rounded-xl border border-rule p-5 space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-1">
          Index insights
        </h2>
        <p className="text-xs text-stone-500 leading-relaxed">
          Krishi Bhoomi Score (300–900) for this {scopeLabel.toLowerCase()}. Field-health only —
          not a credit score or default probability.
        </p>
      </div>

      <div className="grid sm:grid-cols-3 gap-3">
        <div className="bg-paper rounded-lg border border-rule p-3.5">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1.5">
            KBS score
          </p>
          <p
            className="font-bold text-stone-900 text-lg font-mono"
            style={{ color: band?.ink }}
          >
            {kbs != null ? `${kbs}` : '—'}
            <span className="text-xs font-semibold text-stone-500 ml-1">/ {KBS_MAX}</span>
          </p>
          {band && (
            <p className="text-[11px] mt-1 font-medium" style={{ color: band.ink }}>
              {band.name}
            </p>
          )}
        </div>
        <div className="bg-paper rounded-lg border border-rule p-3.5">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1.5">
            Raw (mapped)
          </p>
          <p className="font-semibold text-stone-900 text-sm font-mono">
            {rawKbs != null ? rawKbs : formatScoreOne(view.rawIndex)}
          </p>
          <p className="text-[10px] text-ink-muted mt-1">
            Internal {formatScoreOne(view.rawIndex)}
          </p>
        </div>
        <div className="bg-paper rounded-lg border border-rule p-3.5">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1.5">
            Confidence gate
          </p>
          <p className="font-semibold text-stone-900 text-sm font-mono">
            {formatGateMultiplier(view.gate)}
          </p>
        </div>
      </div>

      {view.weakSubIndices.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-2">
            Weak sub-indices
          </p>
          <div className="flex flex-wrap gap-2">
            {view.weakSubIndices.map((k) => (
              <span
                key={k}
                className="text-xs px-2.5 py-1 rounded-full border border-amber-200 bg-amber-50 text-amber-900"
              >
                {subIndexLabel(k)}
              </span>
            ))}
          </div>
        </div>
      )}

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
