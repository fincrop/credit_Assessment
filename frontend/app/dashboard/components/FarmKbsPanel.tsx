'use client';

import type { RiskView } from '../../lib/useRiskView';
import {
  formatScoreOne,
  resolveWeights,
  subIndexLabel,
  SUBSTANTIVE_SUBINDEX_KEYS,
} from '../../lib/formatRisk';
import {
  toKbsScore,
  kbsBandForScore,
  bandChipStyle,
  scoreColor,
  bandCardSurface,
  KBS_MAX,
} from '../../lib/kbsScore';
import { KbsGauge } from './KbsGauge';
import { polarityIcon, polarityBgClass } from '../../lib/formatRisk';

/**
 * Compact KBS block for a single farm (plot-level score).
 */
export function FarmKbsPanel({
  view,
  fallbackMessage,
}: {
  view: RiskView | null;
  fallbackMessage?: string;
}) {
  if (!view || view.score == null) {
    return (
      <div className="bg-white rounded-xl border border-[#E4DFD4] p-4 h-full flex flex-col justify-center">
        <h2 className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider mb-1 bg-emerald-50/80 inline-block px-1.5 py-0.5 rounded">
          Krishi Bhoomi Score (KBS)
        </h2>
        <p className="text-sm text-stone-500 mt-2">
          {fallbackMessage || 'Plot score appears when this farm is scored.'}
        </p>
      </div>
    );
  }

  const kbs = toKbsScore(view.score);
  const band = kbsBandForScore(kbs);
  const surface = bandCardSurface(band);
  const weights = resolveWeights(view.weights);
  const pillars = SUBSTANTIVE_SUBINDEX_KEYS.filter(
    (k) => view.subIndices[k] != null || weights[k] != null
  );
  const reasons = (view.reasonCodes || []).slice(0, 3);

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-3.5 h-full flex flex-col">
      <h2 className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider mb-0.5 bg-emerald-50/80 inline-block px-1.5 py-0.5 rounded">
        Krishi Bhoomi Score (KBS)
      </h2>
      <p className="text-[11px] text-stone-500 mb-2 leading-snug">
        Plot field-health index, scored 300–900.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-[65%_1fr] gap-2.5 items-center">
        <div className="min-w-0">
          <KbsGauge score={kbs} band={band} compact />
        </div>
        <div
          className="relative overflow-hidden rounded-xl border-2 p-3 flex flex-col gap-2 self-center"
          style={{ background: surface.background, borderColor: surface.border }}
        >
          <div
            className="absolute top-0 left-0 right-0 h-1"
            style={{ background: surface.accent }}
            aria-hidden
          />
          <p
            className="text-[10px] font-bold uppercase tracking-wider"
            style={{ color: surface.ink }}
          >
            Overall risk
          </p>
          {band && (
            <span
              className="inline-flex self-start items-center px-2.5 py-1 rounded-full text-xs font-bold border shadow-sm"
              style={bandChipStyle(band)}
            >
              {band.riskLabel} risk
            </span>
          )}
          {kbs != null && (
            <div>
              <p
                className="text-2xl font-bold font-mono tabular-nums leading-none"
                style={{ color: surface.ink }}
              >
                {kbs}
                <span className="text-sm font-semibold text-stone-500 ml-1">/ {KBS_MAX}</span>
              </p>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-stone-500 mt-1">
                KBS · {band?.name || '—'}
              </p>
            </div>
          )}
        </div>
      </div>

      {band && (
        <div className="mt-2 rounded-lg border border-[#E4DFD4] bg-[#F5F2EB]/40 px-2.5 py-2">
          <p className="text-xs text-stone-700 leading-snug">
            Score sits in the <span className="font-semibold">{band.name}</span> band —{' '}
            {band.shortDescription}
          </p>
        </div>
      )}

      {pillars.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {pillars.map((k) => {
            const v = view.subIndices[k] ?? 0;
            const w = weights[k];
            const pct = Math.max(0, Math.min(100, v));
            return (
              <div
                key={k}
                className="rounded-md border border-[#E4DFD4] bg-[#F5F2EB]/40 px-2 py-1.5 space-y-1"
              >
                <div className="flex justify-between gap-1">
                  <span className="text-[10px] font-semibold text-stone-700 leading-tight line-clamp-2">
                    {subIndexLabel(k)}
                  </span>
                  <span className="text-xs font-bold font-mono tabular-nums shrink-0">
                    {formatScoreOne(v)}
                  </span>
                </div>
                <div className="h-1 bg-[#E8E4DB] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${pct}%`, background: scoreColor(pct) }}
                  />
                </div>
                {w != null && (
                  <p className="text-[10px] text-stone-500">weight {formatScoreOne(w)}%</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {reasons.length > 0 && (
        <ul className="mt-2 space-y-1">
          {reasons.map((rc, i) => (
            <li
              key={i}
              className={`flex gap-2 text-[11px] rounded-md border px-2 py-1.5 ${polarityBgClass(rc.polarity)}`}
            >
              <span className="shrink-0">{polarityIcon(rc.polarity)}</span>
              <span className="leading-snug">{rc.message || rc.code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
