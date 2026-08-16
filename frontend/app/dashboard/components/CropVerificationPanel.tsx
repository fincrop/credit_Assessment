'use client';

import type { CropVerification } from '../../types/assessment';
import { CHROME, VEGETATION_LINE } from '../../lib/vizPalette';
import { ChartFrame, type TableColumn } from './ChartFrame';

/**
 * Does the declared crop match what actually grew?
 *
 * The fraud-or-error signal a lender most wants, and the one most easily
 * mis-framed. `inconsistent` is as often a data-entry slip or a mid-season
 * change of plan as it is misrepresentation, so the wording here stays
 * neutral and descriptive: what was declared, what the phenology looked like,
 * and where they diverge. No accusation, and no exoneration either.
 *
 * The reference band is the honest comparison: every crop has an expected
 * duration range, and showing the observed cycle against that range lets a
 * reader judge the verdict rather than take it.
 */

type CycleCheck = {
  duration_days?: number;
  peak_ndvi?: number;
  season_type?: string;
  duration_ok?: boolean;
  peak_ok?: boolean;
  season_ok?: boolean;
  [k: string]: unknown;
};

const VERDICT: Record<string, { label: string; ink: string; surface: string; border: string }> = {
  consistent: { label: 'Consistent', ink: '#006446', surface: '#DFF0E9', border: '#C4E2D6' },
  inconsistent: { label: 'Does not match', ink: '#9A2E1F', surface: '#FAE8E4', border: '#F2D2CB' },
  indeterminate: { label: 'Cannot say', ink: '#57534E', surface: '#F0EDE6', border: '#E4DFD4' },
};

const TABLE_COLUMNS: TableColumn<CycleCheck>[] = [
  { header: 'Season', cell: (c) => c.season_type ?? '—' },
  { header: 'Duration', numeric: true, cell: (c) => (c.duration_days != null ? `${c.duration_days} d` : '—') },
  { header: 'Peak NDVI', numeric: true, cell: (c) => (c.peak_ndvi != null ? c.peak_ndvi.toFixed(2) : '—') },
  { header: 'Matches', cell: (c) => (c.duration_ok && c.peak_ok ? 'yes' : 'no') },
];

export function CropVerificationPanel({
  verification,
}: {
  verification: CropVerification | null | undefined;
}) {
  if (!verification) {
    return (
      <ChartFrame
        title="Declared crop vs observed growth"
        subtitle="Whether the crop on record matches the growth pattern we measured."
        empty="No crop verification was stored for this assessment. It runs on new assessments; a re-run will populate it."
      />
    );
  }

  const v = VERDICT[String(verification.outcome)] ?? VERDICT.indeterminate;
  const ev = verification.evidence as
    | {
        reference?: { min_days?: number; typical_days?: number; max_days?: number; season?: string; expected_peak_ndvi?: number };
        tolerances?: { duration?: number; peak_ndvi?: number };
        cycles?: CycleCheck[];
        n_cycles_checked?: number;
        n_fully_consistent?: number;
        consistent_share?: number;
      }
    | undefined;

  const ref = ev?.reference;
  const cycles = ev?.cycles ?? [];

  // Axis spans the reference range plus tolerance, so a cycle just outside
  // the band is visibly just outside rather than clipped to the edge.
  const lo = ref?.min_days != null ? ref.min_days * 0.6 : 0;
  const hi = ref?.max_days != null ? ref.max_days * 1.5 : 240;
  const span = Math.max(1, hi - lo);
  const pos = (d: number) => `${Math.max(0, Math.min(100, ((d - lo) / span) * 100))}%`;

  return (
    <ChartFrame
      title="Declared crop vs observed growth"
      subtitle="Whether the crop on record matches the growth pattern measured from orbit."
      tableRows={cycles}
      tableColumns={TABLE_COLUMNS}
      note={
        <>
          A mismatch is as often a data-entry error or a change of plan mid-season
          as it is a misstatement. It is a prompt to ask, not a finding of fact.
          {ev?.consistent_share != null && (
            <>
              {' '}
              {ev.n_fully_consistent ?? 0} of {ev.n_cycles_checked ?? 0} observed cycles
              matched the reference for this crop.
            </>
          )}
        </>
      }
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
            Declared
          </p>
          <p className="text-[15px] font-semibold text-ink mt-0.5">
            {verification.declared_crop || 'none declared'}
          </p>
        </div>
        <span
          className="inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-bold border shrink-0"
          style={{ background: v.surface, color: v.ink, borderColor: v.border }}
        >
          {v.label}
        </span>
      </div>

      {verification.reason && (
        <p className="text-[12px] text-ink-2 mt-2 leading-relaxed">{verification.reason}</p>
      )}

      {ref?.min_days != null && ref?.max_days != null && (
        <div className="mt-4">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted mb-2">
            Cycle length against the reference for {verification.canonical_crop ?? 'this crop'}
          </p>
          <div className="relative h-9">
            {/* Reference band — where a cycle of this crop is expected to land. */}
            <div className="absolute inset-x-0 top-3 h-3 rounded-sm" style={{ background: CHROME.grid }} />
            <div
              className="absolute top-3 h-3 rounded-sm"
              style={{
                left: pos(ref.min_days),
                width: `calc(${pos(ref.max_days)} - ${pos(ref.min_days)})`,
                background: '#DDEAC4',
              }}
            />
            {ref.typical_days != null && (
              <div
                className="absolute top-2 h-5 w-[2px]"
                style={{ left: pos(ref.typical_days), background: VEGETATION_LINE }}
                title={`Typical ${ref.typical_days} days`}
              />
            )}
            {/* Observed cycles. */}
            {cycles.map((c, i) =>
              c.duration_days == null ? null : (
                <div
                  key={i}
                  className="absolute top-1 w-2.5 h-2.5 rounded-full border-2 border-white"
                  style={{
                    left: `calc(${pos(c.duration_days)} - 5px)`,
                    background: c.duration_ok ? VEGETATION_LINE : '#B93A28',
                  }}
                  title={`Observed ${c.duration_days} days${c.season_type ? ` · ${c.season_type}` : ''}`}
                />
              )
            )}
          </div>
          <div className="flex justify-between text-[10px] text-ink-muted font-mono">
            <span>{Math.round(lo)} d</span>
            <span>
              expected {ref.min_days}–{ref.max_days} d
            </span>
            <span>{Math.round(hi)} d</span>
          </div>
        </div>
      )}
    </ChartFrame>
  );
}
