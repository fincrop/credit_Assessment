'use client';

import { useMemo } from 'react';
import type { ReportNdviTrajectory, SignalSource } from '../../types/report';
import { linearScale, linePath, areaPath, segments, ticks, defsId } from '../../lib/chart';
import { VEGETATION_LINE, CHROME } from '../../lib/vizPalette';
import { ChartFrame, Legend, LegendItem, type TableColumn } from './ChartFrame';

/**
 * The parcel's NDVI trajectory, with per-point provenance.
 *
 * The most persuasive artefact the pipeline produces, and it was not on screen
 * at all. Three rules govern how it is drawn:
 *
 * ① GAPS STAY GAPS. No line is drawn across a bin with no observation. A line
 *   through a cloudy fortnight asserts a measurement that was never taken —
 *   the exact fabrication the backend spent v6 eliminating. The gap is drawn
 *   as a hatched band instead, so absence is visible rather than smoothed.
 *
 * ② PROVENANCE IS TEXTURE, NOT HUE. Optical is solid, fused dashed, SAR
 *   dash-dot, imputed dotted. These are the SAME quantity observed with
 *   different confidence; a hue change would imply a different measurement.
 *   Hue stays reserved for magnitude.
 *
 * ③ NO SYNTHESISED PEER LINE. `comparison_available` is false until a cohort
 *   is warm, and the payload carries the reason. An invented district median
 *   would be a fabrication in the most visually persuasive part of the report,
 *   which is the worst possible place for one.
 */

const OBSERVED: SignalSource[] = ['optical', 'fused'];

const SOURCE_STYLE: Record<string, { dash?: string; label: string; observed: boolean }> = {
  optical: { label: 'Optical (Sentinel-2)', observed: true },
  fused: { dash: '6 3', label: 'Fused optical + radar', observed: true },
  sar: { dash: '2 3', label: 'Radar only (cloud)', observed: false },
  imputed: { dash: '1 3', label: 'Reconstructed', observed: false },
};

interface Pt {
  i: number;
  date: string;
  value: number | null;
  source: string | null;
  x: number;
  y: number | null;
}

const TABLE_COLUMNS: TableColumn<Pt>[] = [
  { header: 'Date', cell: (p) => p.date },
  { header: 'NDVI', numeric: true, cell: (p) => (p.value == null ? '—' : p.value.toFixed(3)) },
  {
    header: 'Source',
    cell: (p) => (p.value == null ? 'no observation' : SOURCE_STYLE[p.source ?? '']?.label ?? p.source ?? '—'),
  },
];

export function NdviTrajectory({
  trajectory,
  windowLabel,
}: {
  trajectory: ReportNdviTrajectory | null | undefined;
  windowLabel?: string;
}) {
  const W = 720;
  const H = 220;
  const PAD = { top: 12, right: 12, bottom: 26, left: 34 };

  const model = useMemo(() => {
    if (!trajectory?.dates?.length) return null;
    const { dates, ndvi, signal_source } = trajectory;

    const x = linearScale([0, Math.max(1, dates.length - 1)], [PAD.left, W - PAD.right]);

    // Domain from the observed values only, floored at 0 and padded — an
    // auto-domain that starts at the minimum exaggerates small variation into
    // a dramatic curve.
    const present = ndvi.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    const hi = present.length ? Math.max(...present) : 1;
    const y = linearScale([0, Math.max(0.4, Math.ceil(hi * 10 + 1) / 10)], [H - PAD.bottom, PAD.top]);

    const points: Pt[] = dates.map((d, i) => {
      const v = ndvi[i];
      const value = typeof v === 'number' && Number.isFinite(v) ? v : null;
      return {
        i,
        date: String(d),
        value,
        source: (signal_source?.[i] as string | undefined) ?? null,
        x: x(i),
        y: value == null ? null : y(value),
      };
    });

    // Runs of consecutive bins sharing a provenance, so each run can carry its
    // own dash pattern without breaking the line into per-segment noise.
    const runs: { source: string; pts: Pt[] }[] = [];
    for (const p of points) {
      if (p.y == null) continue;
      const src = p.source ?? 'optical';
      const last = runs[runs.length - 1];
      if (last && last.source === src && last.pts[last.pts.length - 1].i === p.i - 1) {
        last.pts.push(p);
      } else {
        // Carry one point over so runs join up rather than showing false gaps
        // at every provenance change.
        const bridge = last && last.pts[last.pts.length - 1].i === p.i - 1
          ? [last.pts[last.pts.length - 1]]
          : [];
        runs.push({ source: src, pts: [...bridge, p] });
      }
    }

    // Bins with no observation at all, grouped into contiguous blind spans.
    const gaps: { from: number; to: number; n: number }[] = [];
    let g: { from: number; to: number; n: number } | null = null;
    for (const p of points) {
      if (p.value == null) {
        if (g) {
          g.to = p.x;
          g.n += 1;
        } else {
          g = { from: p.x, to: p.x, n: 1 };
        }
      } else if (g) {
        gaps.push(g);
        g = null;
      }
    }
    if (g) gaps.push(g);

    const usedSources = Array.from(
      new Set(points.filter((p) => p.value != null).map((p) => p.source ?? 'optical'))
    );

    return { x, y, points, runs, gaps, usedSources, baselineY: H - PAD.bottom };
  }, [trajectory]);

  if (!model) {
    return (
      <ChartFrame
        title="Vegetation trajectory"
        subtitle="NDVI measured over this parcel."
        empty="No observation series was stored for this assessment. Evidence is written on new runs; a re-assessment will populate it."
      />
    );
  }

  const { x, y, points, runs, gaps, usedSources, baselineY } = model;
  const gradId = defsId('ndvi-fill', trajectory!.dates[0] ?? '0');
  const hatchId = defsId('ndvi-gap', trajectory!.dates[0] ?? '0');

  const observedCount = points.filter(
    (p) => p.value != null && OBSERVED.includes((p.source ?? 'optical') as SignalSource)
  ).length;

  const yTicks = ticks(y.domain, 4);
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));

  return (
    <ChartFrame
      title="Vegetation trajectory"
      subtitle={`NDVI measured over this parcel${windowLabel ? `, ${windowLabel}` : ''}. Higher is more vigorous green cover.`}
      tableRows={points}
      tableColumns={TABLE_COLUMNS}
      legend={
        <Legend>
          {usedSources.map((s) => (
            <LegendItem
              key={s}
              color={VEGETATION_LINE}
              dash={SOURCE_STYLE[s]?.dash}
              label={SOURCE_STYLE[s]?.label ?? s}
            />
          ))}
          {gaps.length > 0 && <LegendItem color={CHROME.missing} label="No observation" />}
        </Legend>
      }
      note={
        <>
          {trajectory!.n_present != null && trajectory!.n_total != null && (
            <>
              {trajectory!.n_present} of {trajectory!.n_total} bins carried a value
              {observedCount > 0 && `, ${observedCount} of them from direct observation`}.{' '}
            </>
          )}
          Nothing is drawn across a bin with no observation — a line through a gap
          would assert a measurement that was never taken.
          {trajectory!.comparison_available === false && trajectory!.comparison_note && (
            <> {trajectory!.comparison_note}</>
          )}
        </>
      }
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label={`NDVI trajectory across ${points.length} observation bins`}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            {/* Vertical only. The value is the line's y-position, so the fade
                never runs along an axis that encodes anything. */}
            <stop offset="0%" stopColor={VEGETATION_LINE} stopOpacity="0.18" />
            <stop offset="100%" stopColor={VEGETATION_LINE} stopOpacity="0" />
          </linearGradient>
          <pattern id={hatchId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill={CHROME.missing} />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#DDD8CC" strokeWidth="2" />
          </pattern>
        </defs>

        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              y1={y(t)}
              x2={W - PAD.right}
              y2={y(t)}
              stroke={CHROME.grid}
              strokeWidth="1"
            />
            <text
              x={PAD.left - 6}
              y={y(t) + 3}
              textAnchor="end"
              style={{ fontSize: 10 }}
              fill={CHROME.label}
            >
              {t.toFixed(1)}
            </text>
          </g>
        ))}

        {/* Blind spans, drawn before the data so the line sits on top. */}
        {gaps.map((g, i) => {
          const half = points.length > 1 ? (x(1) - x(0)) / 2 : 6;
          return (
            <rect
              key={i}
              x={g.from - half}
              y={PAD.top}
              width={Math.max(4, g.to - g.from + half * 2)}
              height={baselineY - PAD.top}
              fill={`url(#${hatchId})`}
            >
              <title>{`${g.n} bin(s) with no observation`}</title>
            </rect>
          );
        })}

        {/* Area under each contiguous run — never spanning a gap. */}
        {segments(points).map((run, i) => (
          <path
            key={`a-${i}`}
            d={areaPath(run.map((p) => ({ x: p.x, y: p.y as number })), baselineY)}
            fill={`url(#${gradId})`}
          />
        ))}

        {/* One stroke per provenance run. Texture carries confidence. */}
        {runs.map((r, i) => (
          <path
            key={`l-${i}`}
            d={linePath(r.pts)}
            fill="none"
            stroke={VEGETATION_LINE}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={SOURCE_STYLE[r.source]?.dash}
          />
        ))}

        {/* Markers only on directly-observed bins — a dot asserts "we saw
            this", so reconstructed values do not get one. */}
        {points
          .filter((p) => p.y != null && OBSERVED.includes((p.source ?? 'optical') as SignalSource))
          .map((p) => (
            <circle
              key={p.i}
              cx={p.x}
              cy={p.y as number}
              r={3}
              fill={VEGETATION_LINE}
              stroke="#FFFFFF"
              strokeWidth="1.5"
            >
              <title>{`${p.date} · NDVI ${p.value!.toFixed(3)} · ${SOURCE_STYLE[p.source ?? 'optical']?.label ?? p.source}`}</title>
            </circle>
          ))}

        <line
          x1={PAD.left}
          y1={baselineY}
          x2={W - PAD.right}
          y2={baselineY}
          stroke={CHROME.axis}
          strokeWidth="1"
        />
        {points
          .filter((_, i) => i % labelEvery === 0)
          .map((p) => (
            <text
              key={`x-${p.i}`}
              x={p.x}
              y={H - 8}
              textAnchor="middle"
              style={{ fontSize: 10 }}
              fill={CHROME.label}
            >
              {p.date.slice(5)}
            </text>
          ))}
      </svg>
    </ChartFrame>
  );
}
