'use client';

import type { DataSufficiency } from '../../types/assessment';
import type { ReportNdviTrajectory, SignalSource } from '../../types/report';
import { CHROME } from '../../lib/vizPalette';
import { ChartFrame, Legend, LegendItem, type TableColumn } from './ChartFrame';

/**
 * Which weeks we could actually see the field.
 *
 * This is the panel that separates "poor land" from "we could not look", and
 * it is the strongest answer to the question a lender will ask about any low
 * score. The trajectory shows what we measured; this shows what we MISSED,
 * which the trajectory can only imply.
 *
 * Two sources, deliberately:
 *   - the per-bin provenance column, when evidence is stored (exact)
 *   - the sufficiency counters, when it is not (aggregate, still useful)
 *
 * Never synthesise the per-bin picture from the counters. Forty-one observed
 * bins out of fifty-two does not tell you WHICH eleven were blind, and a
 * plausible-looking arrangement of cells would be invented data in a panel
 * whose entire purpose is to be honest about absence.
 */

const CELL: Record<string, { fill: string; label: string }> = {
  optical: { fill: '#468526', label: 'Optical' },
  fused: { fill: '#8AB24F', label: 'Fused' },
  sar: { fill: '#B6DEF7', label: 'Radar only' },
  imputed: { fill: '#EFEBE1', label: 'Reconstructed' },
  missing: { fill: '#E4DFD4', label: 'No observation' },
};

interface Cell {
  date: string;
  source: string;
}

const TABLE_COLUMNS: TableColumn<Cell>[] = [
  { header: 'Bin', cell: (c) => c.date },
  { header: 'Observation', cell: (c) => CELL[c.source]?.label ?? c.source },
];

function Figure({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-rule bg-paper/60 px-3 py-2">
      <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-wider">{label}</p>
      <p className="text-[15px] font-mono tabular-nums text-ink mt-0.5 leading-none">{value}</p>
      {hint && <p className="text-[10px] text-ink-muted mt-1 leading-snug">{hint}</p>}
    </div>
  );
}

const pct = (f: number | null | undefined) =>
  typeof f === 'number' && Number.isFinite(f) ? `${Math.round(f * 100)}%` : '—';

export function ObservationCalendar({
  sufficiency,
  trajectory,
}: {
  sufficiency: DataSufficiency | null | undefined;
  trajectory?: ReportNdviTrajectory | null;
}) {
  const ev = sufficiency?.evidence;

  const cells: Cell[] =
    trajectory?.dates?.length && trajectory.signal_source?.length
      ? trajectory.dates.map((d, i) => {
          const v = trajectory.ndvi[i];
          const observed = typeof v === 'number' && Number.isFinite(v);
          const src = (trajectory.signal_source?.[i] as SignalSource | null) ?? null;
          return { date: String(d), source: observed && src ? String(src) : 'missing' };
        })
      : [];

  const stats = (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
      <Figure
        label="Bins observed"
        value={
          ev?.n_observed_bins != null && ev?.n_bins != null
            ? `${ev.n_observed_bins}/${ev.n_bins}`
            : '—'
        }
        hint={
          ev?.thresholds?.min_observed_fraction != null
            ? `Floor ${pct(ev.thresholds.min_observed_fraction)}`
            : undefined
        }
      />
      <Figure label="Clear coverage" value={pct(ev?.observed_fraction)} />
      <Figure
        label="Longest blind gap"
        value={ev?.largest_blind_gap_days != null ? `${ev.largest_blind_gap_days} d` : '—'}
        hint={
          ev?.thresholds?.blind_gap_days != null
            ? `Limit ${ev.thresholds.blind_gap_days} d`
            : undefined
        }
      />
      <Figure
        label="Radar-only bins"
        value={ev?.n_sar_only_bins ?? '—'}
        hint="Measured, lower confidence"
      />
    </div>
  );

  if (!ev && cells.length === 0) {
    return (
      <ChartFrame
        title="What we could see"
        subtitle="Observation coverage across the assessment window."
        empty="No observation record was stored for this assessment. Coverage is written on new runs; a re-assessment will populate it."
      />
    );
  }

  const usedSources = Array.from(new Set(cells.map((c) => c.source)));

  return (
    <ChartFrame
      title="What we could see"
      subtitle="Each cell is one observation bin across the assessment window. A score is only as good as the coverage behind it."
      tableRows={cells}
      tableColumns={TABLE_COLUMNS}
      legend={
        cells.length > 0 ? (
          <Legend>
            {usedSources.map((s) => (
              <LegendItem key={s} color={CELL[s]?.fill ?? CHROME.missing} label={CELL[s]?.label ?? s} />
            ))}
          </Legend>
        ) : undefined
      }
      note={
        cells.length === 0
          ? 'Per-bin coverage is not stored for this assessment, so only the totals are shown. The individual blind weeks cannot be reconstructed from the totals, and guessing at them would invent data in the one panel that exists to be honest about absence.'
          : sufficiency?.reason
            ? `Sufficiency verdict: ${sufficiency.reason}`
            : undefined
      }
    >
      {cells.length > 0 && (
        <div
          className="flex flex-wrap gap-[3px]"
          role="img"
          aria-label={`${cells.filter((c) => c.source !== 'missing').length} of ${cells.length} bins carried an observation`}
        >
          {cells.map((c, i) => (
            <div
              key={i}
              className="w-3.5 h-3.5 rounded-[2px] shrink-0"
              style={{ background: CELL[c.source]?.fill ?? CHROME.missing }}
              title={`${c.date} · ${CELL[c.source]?.label ?? c.source}`}
            />
          ))}
        </div>
      )}
      {stats}
    </ChartFrame>
  );
}
