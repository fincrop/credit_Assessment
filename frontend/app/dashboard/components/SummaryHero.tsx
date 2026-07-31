'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { useRiskView } from '../../lib/useRiskView';
import { formatScoreWhole, formatScoreOne } from '../../lib/formatRisk';
import { riskBgClass } from '../../lib/format';
import { SubIndexBars } from './SubIndexBars';

export function SummaryHero({ data }: { data: AssessmentPayload }) {
  const view = useRiskView(data);
  const score = view.score;
  const pct = typeof score === 'number' ? Math.min(100, Math.max(0, score)) : 0;
  const scoreColor = pct >= 70 ? '#16a34a' : pct >= 45 ? '#d97706' : '#dc2626';

  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const dash = (pct / 100) * circumference;

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-1">
        Agronomic Risk Index
      </h2>
      <p className="text-xs text-stone-500 mb-5 leading-relaxed max-w-2xl">
        Expert-weighted field-health index (0–100). This is <strong>not</strong> a loan amount,
        credit limit, or probability of default.
      </p>

      <div className="flex flex-wrap gap-6 items-center">
        <div className="relative flex-shrink-0">
          <svg width="130" height="130" viewBox="0 0 130 130">
            <circle cx="65" cy="65" r={radius} fill="none" stroke="#E8E4DB" strokeWidth="10" />
            <circle
              cx="65"
              cy="65"
              r={radius}
              fill="none"
              stroke={scoreColor}
              strokeWidth="10"
              strokeDasharray={`${dash} ${circumference}`}
              strokeDashoffset={circumference * 0.25}
              strokeLinecap="round"
              transform="rotate(-90 65 65)"
              style={{ transition: 'stroke-dasharray 0.8s ease' }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-2xl font-bold" style={{ color: scoreColor }}>
              {formatScoreWhole(score)}
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
            {view.indexVersion && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-[#F5F2EB] border border-[#E4DFD4] text-[11px] text-stone-600 font-mono">
                {view.indexVersion}
              </span>
            )}
            {view.method && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-[#F5F2EB] border border-[#E4DFD4] text-[11px] text-stone-500 font-mono">
                {view.method}
              </span>
            )}
          </div>
          {view.rawIndex != null &&
            view.score != null &&
            Math.abs(view.rawIndex - view.score) >= 0.5 && (
              <p className="text-xs text-amber-800">
                Raw {formatScoreOne(view.rawIndex)} → gated {formatScoreOne(view.score)}
              </p>
            )}
          {data.processing_time_seconds != null && (
            <p className="text-[11px] text-stone-400">
              Processed in {formatScoreOne(data.processing_time_seconds)}s ·{' '}
              {(data.pipeline_stages ?? []).length} pipeline stages
            </p>
          )}
        </div>
      </div>

      <div className="mt-5 pt-5 border-t border-[#E4DFD4]">
        <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-3">
          Sub-indices
        </p>
        <SubIndexBars view={view} />
      </div>

      {view.scoringNarrative && (
        <p className="mt-4 text-[12px] text-stone-500 leading-relaxed border-t border-[#E4DFD4] pt-4">
          {view.scoringNarrative}
        </p>
      )}
    </div>
  );
}
