'use client';

import type { RiskView } from '../../lib/useRiskView';
import type { LandCover, LandCoverStream } from '../../types/assessment';
import { polarityBgClass, polarityIcon } from '../../lib/formatRisk';
import { landCoverLabel } from '../../lib/terminalState';

const STREAM_LABELS: Record<string, string> = {
  spectral: 'Spectral',
  temporal: 'Seasonal pattern',
  external_lulc: 'Land-use map',
};

function outcomeCopy(outcome: string | undefined, cls: string | undefined): string {
  if (outcome === 'reject') {
    return `Not scored as farmland — observed as ${landCoverLabel(cls).toLowerCase()}.`;
  }
  if (outcome === 'flag') {
    return `Classified as ${landCoverLabel(cls).toLowerCase()}, with a flag for unusual signals.`;
  }
  if (cls) {
    return `Classified as ${landCoverLabel(cls).toLowerCase()}; scoring proceeded.`;
  }
  return 'No land-cover verdict was stored for this plot.';
}

function StreamVote({ name, stream }: { name: string; stream: LandCoverStream }) {
  const [cls, conf] = stream;
  const pct = typeof conf === 'number' ? Math.max(0, Math.min(100, conf * 100)) : null;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <dt className="text-[11px] text-stone-500">{STREAM_LABELS[name] ?? name}</dt>
        <dd className="text-[12px] font-medium text-stone-800">
          {cls ? landCoverLabel(cls) : '—'}
        </dd>
      </div>
      {pct != null && (
        <div className="h-1 bg-rule-soft rounded-full overflow-hidden mt-1">
          <div className="h-full rounded-full bg-emerald-600/80" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

export function OverviewFindingsPanel({
  view,
  landCover,
  scored = false,
}: {
  view: RiskView | null;
  landCover: LandCover | null | undefined;
  /** True when this plot produced a score — land-cover must have passed. */
  scored?: boolean;
}) {
  const codes = (view?.reasonCodes || []).slice(0, 6);
  const streams = Object.entries(landCover?.evidence?.streams ?? {}).filter(
    (e): e is [string, LandCoverStream] => Array.isArray(e[1])
  );
  const hasVerdict = Boolean(landCover?.class);
  const classified = hasVerdict && landCover ? landCover : null;
  const agreeing = streams.filter(([, s]) => s[0] === landCover?.class).length;
  const confPct =
    typeof landCover?.confidence === 'number'
      ? Math.round(Math.max(0, Math.min(1, landCover.confidence)) * 100)
      : null;

  return (
    <div className="bg-white rounded-xl border border-rule p-4 h-full flex flex-col min-h-0">
      <section className="min-h-0">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
          Findings
        </p>
        <h2 className="text-sm font-bold text-stone-900 mt-0.5">What the score is saying</h2>
        {codes.length === 0 ? (
          <p className="text-xs text-stone-500 mt-3 leading-relaxed">
            No structured findings were stored for this plot.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {codes.map((rc, i) => (
              <li
                key={`${rc.code ?? i}-${i}`}
                className={`flex gap-2 text-xs rounded-lg border px-3 py-2 leading-snug ${polarityBgClass(rc.polarity)}`}
              >
                <span className="font-mono shrink-0" aria-hidden>
                  {polarityIcon(rc.polarity)}
                </span>
                <span>{rc.message || rc.code || '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4 pt-4 border-t border-rule flex-1 min-h-0">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
          Land classification
        </p>
        <h2 className="text-sm font-bold text-stone-900 mt-0.5">What this parcel is</h2>

        {!hasVerdict && scored ? (
          <div className="mt-3 rounded-lg border border-rule bg-paper/60 px-3.5 py-3">
            <p className="text-lg font-bold text-stone-900 leading-none">Cropland</p>
            <p className="text-[12px] text-stone-600 mt-2 leading-snug">
              This plot was scored as farmland. Independent land-cover streams were not
              stored on this run, but scoring only proceeds for cultivable parcels.
            </p>
          </div>
        ) : !hasVerdict ? (
          <p className="text-xs text-stone-500 mt-3 leading-relaxed">
            This plot was not classified — it was not scored as farmland in this run.
          </p>
        ) : classified ? (
          <div className="mt-3 space-y-3">
            <div className="rounded-lg border border-rule bg-paper/60 px-3.5 py-3">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <p className="text-lg font-bold text-stone-900 leading-none">
                  {landCoverLabel(classified.class)}
                </p>
                {confPct != null && (
                  <p className="text-[12px] font-mono text-stone-600 tabular-nums">
                    {confPct}% confidence
                  </p>
                )}
              </div>
              {confPct != null && (
                <div className="h-1.5 bg-rule-soft rounded-full overflow-hidden mt-2">
                  <div
                    className="h-full rounded-full bg-emerald-600"
                    style={{ width: `${confPct}%` }}
                  />
                </div>
              )}
              <p className="text-[12px] text-stone-600 mt-2 leading-snug">
                {outcomeCopy(classified.outcome, classified.class)}
              </p>
              {classified.reason && (
                <p className="text-[11px] text-ink-muted mt-1.5 leading-relaxed">
                  {classified.reason}
                </p>
              )}
            </div>

            {streams.length > 0 && (
              <dl className="space-y-2">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="text-[10px] uppercase tracking-wider font-semibold text-ink-muted">
                    Independent checks
                  </p>
                  <p className="text-[11px] text-ink-muted">
                    {agreeing}/{streams.length} agree
                  </p>
                </div>
                {streams.map(([name, stream]) => (
                  <StreamVote key={name} name={name} stream={stream} />
                ))}
              </dl>
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}
