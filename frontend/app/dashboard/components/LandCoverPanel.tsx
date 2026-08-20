'use client';

import type { LandCover, LandCoverStream } from '../../types/assessment';
import { landCoverLabel } from '../../lib/terminalState';
import { ChartFrame } from './ChartFrame';

/**
 * What kind of land this is, and how confidently.
 *
 * Three independent streams vote — spectral signature, temporal behaviour,
 * and external land-use maps — and they are shown SEPARATELY rather than
 * collapsed into the final verdict. "The classifier says water" and "all
 * three streams independently say water" are different strengths of evidence,
 * and a lender refusing a parcel deserves to see which one they have.
 *
 * There is no stacked composition bar here, deliberately. The gate emits a
 * class per stream, not per-pixel fractions, so a bar showing "62% cropland /
 * 38% built-up" would be invented. The verdicts are what exist.
 */

const STREAM_LABELS: Record<string, string> = {
  spectral: 'Spectral signature',
  temporal: 'Temporal behaviour',
  external_lulc: 'External land-use map',
};

const STREAM_HINTS: Record<string, string> = {
  spectral: 'What the reflectance looks like across bands.',
  temporal: 'Whether it behaves like something that gets sown and harvested.',
  external_lulc: 'What published land-use mapping already says about this location.',
};

function confidenceBar(conf: number) {
  const pct = Math.max(0, Math.min(100, conf * 100));
  return (
    <div className="h-1.5 bg-rule-strong rounded-full overflow-hidden w-full mt-1">
      <div className="h-full rounded-full bg-accent-gold" style={{ width: `${pct}%` }} />
    </div>
  );
}

function StreamRow({ name, stream }: { name: string; stream: LandCoverStream }) {
  const [cls, conf, notes] = stream;
  return (
    <div className="py-2 border-t border-rule first:border-t-0 first:pt-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[12px] font-semibold text-ink-2">
          {STREAM_LABELS[name] ?? name}
        </span>
        <span className="text-[12px] font-mono text-ink shrink-0">
          {cls ? landCoverLabel(cls) : 'no verdict'}
          {typeof conf === 'number' && (
            <span className="text-ink-muted ml-1.5 text-[11px]">{conf.toFixed(2)}</span>
          )}
        </span>
      </div>
      {typeof conf === 'number' && confidenceBar(conf)}
      <p className="text-[10px] text-ink-muted mt-1 leading-snug">
        {STREAM_HINTS[name] ?? ''}
      </p>
      {Array.isArray(notes) && notes.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {notes.map((n, i) => (
            <li key={i} className="text-[11px] text-ink-2 leading-snug">
              · {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function LandCoverPanel({ landCover }: { landCover: LandCover | null | undefined }) {
  if (!landCover) {
    return (
      <ChartFrame
        title="Land classification"
        subtitle="Whether this parcel is farmland at all."
        empty="No land-cover verdict was stored for this assessment. The gate runs on new assessments; a re-run will populate it."
      />
    );
  }

  const streams = Object.entries(landCover.evidence?.streams ?? {}).filter(
    (e): e is [string, LandCoverStream] => Array.isArray(e[1])
  );
  const agreeing = streams.filter(([, s]) => s[0] === landCover.class).length;

  const outcomeCopy =
    landCover.outcome === 'reject'
      ? 'Not scored — this parcel is not farmland.'
      : landCover.outcome === 'flag'
        ? 'Scored, but the classification is unusual and was flagged.'
        : 'Classified as farmland; scoring proceeded.';

  return (
    <ChartFrame
      title="Land classification"
      subtitle="Whether this parcel is farmland at all — checked before any credit signal is computed."
      note={
        streams.length > 0 ? (
          <>
            {agreeing} of {streams.length} streams reached the same verdict independently.
          </>
        ) : undefined
      }
    >
      <div className="rounded-lg border border-rule bg-paper/50 px-3.5 py-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <span className="text-[15px] font-semibold text-ink">
            {landCoverLabel(landCover.class)}
          </span>
          {typeof landCover.confidence === 'number' && (
            <span className="font-mono tabular-nums text-[13px] text-ink-2">
              confidence {landCover.confidence.toFixed(2)}
            </span>
          )}
        </div>
        <p className="text-[12px] text-ink-2 mt-1 leading-snug">{outcomeCopy}</p>
        {landCover.reason && (
          <p className="text-[11px] text-ink-muted mt-1.5 leading-relaxed">
            {landCover.reason}
          </p>
        )}
      </div>

      {streams.length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted mb-1.5">
            Independent streams
          </p>
          {streams.map(([name, s]) => (
            <StreamRow key={name} name={name} stream={s} />
          ))}
        </div>
      )}
    </ChartFrame>
  );
}
