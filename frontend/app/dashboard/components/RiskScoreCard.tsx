'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { useRiskView } from '../../lib/useRiskView';
import { formatScoreWhole } from '../../lib/formatRisk';
import { riskBgClass } from '../../lib/format';
import { SubIndexBars } from './SubIndexBars';

/**
 * Overview risk gauge. HARD RULE: only pass `data` when job SUCCESS.
 * While running, use placeholder mode (data=null).
 */
export function RiskScoreCard({
  data,
  placeholder,
  statusMessage,
}: {
  data: AssessmentPayload | null;
  placeholder?: boolean;
  statusMessage?: string;
}) {
  const showPlaceholder = placeholder || !data;
  const view = useRiskView(data);
  const score = view.score;
  const insufficient = !!view.insufficientData || score == null;
  const pct =
    !showPlaceholder && !insufficient && typeof score === 'number'
      ? Math.min(100, Math.max(0, score))
      : 0;

  const scoreColor = showPlaceholder
    ? '#a8a29e'
    : insufficient
      ? '#a8a29e'
      : pct >= 70
        ? '#16a34a'
        : pct >= 45
          ? '#d97706'
          : '#dc2626';

  const radius = 58;
  const circumference = 2 * Math.PI * radius;
  const dash = (pct / 100) * circumference;

  const highlights: string[] = [];
  if (!showPlaceholder && view.category) {
    highlights.push(`Overall risk: ${view.category}`);
  }
  if (!showPlaceholder && view.nPlotsScored != null && view.nPlotsTotal != null) {
    highlights.push(`${view.nPlotsScored}/${view.nPlotsTotal} plots scored`);
  }
  const topReasons = (view.reasonCodes || []).slice(0, 3);
  for (const r of topReasons) {
    if (r?.message) highlights.push(String(r.message).slice(0, 90));
  }

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 h-full">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-1">
        Agronomic Risk Index
      </h2>
      <p className="text-xs text-stone-500 mb-5 leading-relaxed">
        Field-health index (0–100). Not a loan amount or default probability.
      </p>

      {showPlaceholder ? (
        <div className="flex flex-col sm:flex-row gap-6 items-center">
          <div className="relative flex-shrink-0 opacity-60">
            <svg width="140" height="140" viewBox="0 0 140 140">
              <circle cx="70" cy="70" r={radius} fill="none" stroke="#E8E4DB" strokeWidth="10" />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-2xl font-bold text-stone-400">—</span>
              <span className="text-[10px] text-stone-400 uppercase tracking-widest">Index</span>
            </div>
          </div>
          <div className="flex-1 space-y-2">
            <p className="text-sm font-medium text-stone-600">
              {statusMessage || 'Analyzing plots… farmer score appears when all plots finish.'}
            </p>
            <p className="text-xs text-stone-400">
              Per-farm scores stream into the list below as each plot completes.
            </p>
          </div>
        </div>
      ) : (
        <>
          {insufficient && (
            <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
              Insufficient data to score this farmer
              {view.category === 'INSUFFICIENT_DATA' ? ' (no scorable plots).' : '.'}
            </div>
          )}

          <div className="flex flex-wrap gap-6 items-center">
            <div className="relative flex-shrink-0">
              <svg width="140" height="140" viewBox="0 0 140 140">
                <circle cx="70" cy="70" r={radius} fill="none" stroke="#E8E4DB" strokeWidth="10" />
                {!insufficient && (
                  <circle
                    cx="70"
                    cy="70"
                    r={radius}
                    fill="none"
                    stroke={scoreColor}
                    strokeWidth="10"
                    strokeDasharray={`${dash} ${circumference}`}
                    strokeDashoffset={circumference * 0.25}
                    strokeLinecap="round"
                    transform="rotate(-90 70 70)"
                    style={{ transition: 'stroke-dasharray 0.8s ease' }}
                  />
                )}
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-3xl font-bold" style={{ color: scoreColor }}>
                  {insufficient ? '—' : formatScoreWhole(score)}
                </span>
                <span className="text-[10px] text-stone-400 font-medium uppercase tracking-widest">
                  Index
                </span>
              </div>
            </div>

            <div className="flex-1 min-w-[200px] space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                {view.category && (
                  <span
                    className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold border ${riskBgClass(
                      typeof view.category === 'string' ? view.category : undefined
                    )}`}
                  >
                    {typeof view.category === 'string' ? view.category : '—'}
                  </span>
                )}
              </div>
              <ul className="space-y-1.5">
                {highlights.slice(0, 4).map((h, i) => (
                  <li key={i} className="text-xs text-stone-600 flex gap-2">
                    <span className="text-emerald-600 shrink-0">•</span>
                    <span>{h}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {view.subIndices && Object.keys(view.subIndices).length > 0 && (
            <div className="mt-5 pt-4 border-t border-[#E4DFD4]">
              <SubIndexBars view={view} hideGate />
            </div>
          )}
        </>
      )}
    </div>
  );
}
