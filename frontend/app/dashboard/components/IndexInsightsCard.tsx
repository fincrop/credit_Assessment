'use client';

import type { RiskView } from '../../lib/useRiskView';
import {
  formatScoreOne,
  formatGateMultiplier,
  polarityIcon,
  polarityBgClass,
  subIndexLabel,
} from '../../lib/formatRisk';

export function IndexInsightsCard({ view }: { view: RiskView }) {
  const codes = (view.reasonCodes || []).slice(0, 5);

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">
        Index Insights
      </h2>
      <p className="text-xs text-stone-500 mb-5 leading-relaxed">
        Agronomic risk index — not a loan amount or probability of default. Weights are
        provisional.
      </p>

      <div className="grid sm:grid-cols-3 gap-3 mb-5">
        <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3.5">
          <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">
            Index score
          </p>
          <p className="font-semibold text-stone-900 text-sm font-mono">
            {formatScoreOne(view.score)}
          </p>
        </div>
        <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3.5">
          <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">
            Raw index
          </p>
          <p className="font-semibold text-stone-900 text-sm font-mono">
            {formatScoreOne(view.rawIndex)}
          </p>
        </div>
        <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3.5">
          <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">
            Confidence gate
          </p>
          <p className="font-semibold text-stone-900 text-sm font-mono">
            {formatGateMultiplier(view.gate)}
          </p>
        </div>
      </div>

      {view.weakSubIndices.length > 0 && (
        <div className="mb-4">
          <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-2">
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
        <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-2">
          Reason codes
        </p>
        {codes.length === 0 ? (
          <p className="text-sm text-stone-500">No structured reason codes for this job.</p>
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
