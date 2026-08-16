'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { formatScoreOne } from '../../lib/formatRisk';

export function SignalQualityStrip({ data }: { data: AssessmentPayload }) {
  const sq = data.signal_quality_summary;
  if (!sq || (sq.valid_fraction == null && sq.n_valid_bins == null)) {
    return (
      <div className="bg-white rounded-xl border border-dashed border-rule px-4 py-3">
        <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1">
          Signal quality
        </p>
        <p className="text-xs text-stone-500">
          Not available for this job (older payload or satellite summary not stashed).
        </p>
      </div>
    );
  }

  const validPct =
    typeof sq.valid_fraction === 'number'
      ? sq.valid_fraction <= 1
        ? sq.valid_fraction * 100
        : sq.valid_fraction
      : null;
  const sarPct =
    typeof sq.sar_fallback_fraction === 'number'
      ? sq.sar_fallback_fraction <= 1
        ? sq.sar_fallback_fraction * 100
        : sq.sar_fallback_fraction
      : null;

  return (
    <div className="bg-white rounded-xl border border-rule px-4 py-3">
      <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-2">
        Signal quality
      </p>
      <div className="flex flex-wrap gap-4 text-xs text-stone-700">
        {validPct != null && (
          <span>
            Valid bins:{' '}
            <strong className="font-mono">{formatScoreOne(validPct)}%</strong>
          </span>
        )}
        {sq.n_valid_bins != null && (
          <span>
            n={''}
            <strong className="font-mono">{sq.n_valid_bins}</strong>
          </span>
        )}
        {sarPct != null && (
          <span>
            SAR fallback:{' '}
            <strong className="font-mono">{formatScoreOne(sarPct)}%</strong>
          </span>
        )}
        {sq.mean_bin_quality != null && (
          <span>
            Mean quality:{' '}
            <strong className="font-mono">{formatScoreOne(sq.mean_bin_quality)}</strong>
          </span>
        )}
      </div>
    </div>
  );
}
