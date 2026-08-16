'use client';

import type { ReportPayload } from '../../types/report';
import { bandForIndex, NO_BAND } from '../../lib/kbsScore';

/**
 * Movement since the previous assessment.
 *
 * Renders NOTHING when `trend` is null, which is the whole contract. The
 * payload deliberately omits trend on a first assessment rather than emitting
 * a delta of zero — a "0" or a "—" against a baseline that does not exist
 * invents a history, and the first thing a reader does with a trend chip is
 * believe there was a previous number.
 *
 * There is no sparkline of the full history here yet: the report payload
 * carries only the previous point, not the series. Drawing a two-point line
 * and calling it a trajectory would overstate what two assessments show.
 */

export function ScoreTrendSparkline({ report }: { report: ReportPayload | null | undefined }) {
  const t = report?.trend;
  if (!t || typeof t.delta !== 'number') return null;

  const band = bandForIndex(report?.score?.index_score);
  const rising = t.direction === 'up';
  const flat = t.direction === 'flat';

  // Direction is carried by an arrow and a signed number, not by colour
  // alone — and "up" is not automatically good news to every reader.
  const glyph = flat ? '→' : rising ? '↑' : '↓';
  const ink = flat ? NO_BAND.ink : rising ? '#006446' : '#9A2E1F';

  return (
    <div
      className="inline-flex items-baseline gap-2 rounded-lg border border-rule bg-paper/60 px-3 py-1.5"
      title={`Previous assessment: KBS ${t.previous_kbs}${
        t.previous_date ? ` on ${String(t.previous_date).slice(0, 10)}` : ''
      }`}
    >
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
        Since last
      </span>
      <span className="font-mono tabular-nums text-[14px] font-bold" style={{ color: ink }}>
        {glyph} {t.delta > 0 ? '+' : ''}
        {t.delta}
      </span>
      <span className="text-[11px] text-ink-muted font-mono">
        was {t.previous_kbs}
        {t.previous_date ? ` · ${String(t.previous_date).slice(0, 10)}` : ''}
      </span>
      {band && (
        <span className="text-[11px] text-ink-muted">
          now {band.name.toLowerCase()}
        </span>
      )}
    </div>
  );
}
