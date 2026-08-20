'use client';

import { useMemo, useState } from 'react';
import type { ReportNdviTrajectory, SignalSource } from '../../types/report';
import type { CropCycle } from '../../types/assessment';
import { linearScale, linePath, areaPath, segments, ticks, defsId } from '../../lib/chart';
import { VEGETATION_LINE, CHROME, SERIES } from '../../lib/vizPalette';
import { ChartFrame, Legend, LegendItem, type TableColumn } from './ChartFrame';
import { resolveCropCycles } from '../../lib/ndviCycles';

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
  display: number | null;
  observed: boolean;
  source: string | null;
  x: number;
  y: number | null;
  displayY: number | null;
}

const TABLE_COLUMNS: TableColumn<Pt>[] = [
  { header: 'Date', cell: (p) => p.date },
  { header: 'NDVI', numeric: true, cell: (p) => (p.value == null ? '—' : p.value.toFixed(3)) },
  {
    header: 'Source',
    cell: (p) =>
      p.value == null ? 'no observation' : SOURCE_STYLE[p.source ?? '']?.label ?? p.source ?? '—',
  },
];

function axisDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(5, 10);
  return d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
}

function tooltipDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function cycleTitle(cycle: CropCycle, index: number): string {
  const raw = cycle.season_label || cycle.season_type;
  if (raw) return String(raw).replace(/_/g, ' ');
  return `Season ${index + 1}`;
}

function dateMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isFinite(t) ? t : null;
}

function xAtDate(
  iso: string | null | undefined,
  dates: string[],
  x: (i: number) => number
): number | null {
  const t = dateMs(iso);
  if (t == null || dates.length === 0) return null;
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < dates.length; i++) {
    const di = dateMs(dates[i]);
    if (di == null) continue;
    const dist = Math.abs(di - t);
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return x(best);
}
function fillInteriorGaps(values: (number | null)[]): (number | null)[] {
  const out = values.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null));
  let i = 0;
  while (i < out.length) {
    if (out[i] != null) {
      i += 1;
      continue;
    }
    let next = i + 1;
    while (next < out.length && out[next] == null) next += 1;
    const prev = i - 1;
    if (prev >= 0 && next < out.length && out[prev] != null && out[next] != null) {
      const a = out[prev] as number;
      const b = out[next] as number;
      const span = next - prev;
      for (let k = prev + 1; k < next; k++) {
        out[k] = a + ((b - a) * (k - prev)) / span;
      }
      i = next;
    } else {
      i += 1;
    }
  }
  return out;
}

function NdviChartSvg({
  points,
  runs,
  gaps,
  y,
  x,
  yTicks,
  pad,
  width,
  height,
  baselineY,
  gradId,
  hatchId,
  labelEvery,
  overview,
  cycleBands,
  hover,
  onHover,
}: {
  points: Pt[];
  runs: { source: string; pts: { x: number; y: number }[] }[];
  gaps: { from: number; to: number; n: number }[];
  y: (v: number) => number;
  x: (v: number) => number;
  yTicks: number[];
  pad: { top: number; right: number; bottom: number; left: number };
  width: number;
  height: number;
  baselineY: number;
  gradId: string;
  hatchId: string;
  labelEvery: number;
  overview: boolean;
  cycleBands: { x0: number; x1: number; label: string; color: string }[];
  hover: Pt | null;
  onHover: (pt: Pt | null) => void;
}) {
  const fillPts = overview
    ? points.filter((p) => p.displayY != null).map((p) => ({ x: p.x, y: p.displayY as number }))
    : null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full h-auto"
      role="img"
      aria-label={`NDVI trajectory across ${points.length} observation bins`}
      onMouseLeave={() => onHover(null)}
      onMouseMove={(e) => {
        const svg = e.currentTarget;
        const rect = svg.getBoundingClientRect();
        const svgX = ((e.clientX - rect.left) / rect.width) * width;
        let best: Pt | null = null;
        let bestDist = Infinity;
        for (const p of points) {
          const dist = Math.abs(p.x - svgX);
          if (dist < bestDist) {
            bestDist = dist;
            best = p;
          }
        }
        onHover(best);
      }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={VEGETATION_LINE} stopOpacity="0.22" />
          <stop offset="100%" stopColor={VEGETATION_LINE} stopOpacity="0" />
        </linearGradient>
        <pattern
          id={hatchId}
          width="6"
          height="6"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width="6" height="6" fill={CHROME.missing} />
          <line x1="0" y1="0" x2="0" y2="6" stroke="#DDD8CC" strokeWidth="2" />
        </pattern>
      </defs>

      {yTicks.map((t) => (
        <g key={t}>
          <line
            x1={pad.left}
            y1={y(t)}
            x2={width - pad.right}
            y2={y(t)}
            stroke={CHROME.grid}
            strokeWidth="1"
          />
          <text
            x={pad.left - 6}
            y={y(t) + 3}
            textAnchor="end"
            style={{ fontSize: 10 }}
            fill={CHROME.label}
          >
            {t.toFixed(1)}
          </text>
        </g>
      ))}

      {cycleBands.map((b, i) => (
        <g key={`c-${i}`}>
          <rect
            x={b.x0}
            y={pad.top}
            width={Math.max(2, b.x1 - b.x0)}
            height={baselineY - pad.top}
            fill={b.color}
            opacity={0.12}
          />
          <line
            x1={b.x0}
            y1={pad.top}
            x2={b.x0}
            y2={baselineY}
            stroke={b.color}
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity={0.7}
          />
          <line
            x1={b.x1}
            y1={pad.top}
            x2={b.x1}
            y2={baselineY}
            stroke={b.color}
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity={0.7}
          />
          {b.x1 - b.x0 > 36 && (
            <text
              x={(b.x0 + b.x1) / 2}
              y={pad.top + 11}
              textAnchor="middle"
              style={{ fontSize: 9, fontWeight: 600 }}
              fill={b.color}
            >
              {b.label}
            </text>
          )}
        </g>
      ))}

      {!overview &&
        gaps.map((g, i) => {
          const half = points.length > 1 ? (x(1) - x(0)) / 2 : 6;
          return (
            <rect
              key={i}
              x={g.from - half}
              y={pad.top}
              width={Math.max(4, g.to - g.from + half * 2)}
              height={baselineY - pad.top}
              fill={`url(#${hatchId})`}
            >
              <title>{`${g.n} bin(s) with no observation`}</title>
            </rect>
          );
        })}

      {overview && fillPts && fillPts.length > 1 && (
        <path d={areaPath(fillPts, baselineY)} fill={`url(#${gradId})`} />
      )}
      {!overview &&
        segments(points).map((run, i) => (
          <path
            key={`a-${i}`}
            d={areaPath(
              run.map((p) => ({ x: p.x, y: p.y as number })),
              baselineY
            )}
            fill={`url(#${gradId})`}
          />
        ))}

      {overview && fillPts && fillPts.length > 1 && (
        <path
          d={linePath(fillPts)}
          fill="none"
          stroke={VEGETATION_LINE}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {!overview &&
        runs.map((r, i) => (
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

      {points
        .filter(
          (p) =>
            p.y != null &&
            p.observed &&
            OBSERVED.includes((p.source ?? 'optical') as SignalSource)
        )
        .map((p) => (
          <circle
            key={p.i}
            cx={p.x}
            cy={(overview ? p.displayY : p.y) as number}
            r={overview ? 2.5 : 3}
            fill={VEGETATION_LINE}
            stroke="#FFFFFF"
            strokeWidth="1.5"
          >
            <title>{`${p.date} · NDVI ${p.value!.toFixed(3)}`}</title>
          </circle>
        ))}

      <line
        x1={pad.left}
        y1={baselineY}
        x2={width - pad.right}
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
            y={height - 8}
            textAnchor="middle"
            style={{ fontSize: 10 }}
            fill={CHROME.label}
          >
            {axisDate(p.date)}
          </text>
        ))}

      {hover && (hover.displayY != null || hover.y != null) && (
        <g pointerEvents="none">
          <line
            x1={hover.x}
            y1={pad.top}
            x2={hover.x}
            y2={baselineY}
            stroke={CHROME.labelStrong}
            strokeWidth="1"
            strokeDasharray="2 3"
            opacity={0.55}
          />
          <circle
            cx={hover.x}
            cy={(overview ? hover.displayY ?? hover.y : hover.y ?? hover.displayY) as number}
            r={5}
            fill={VEGETATION_LINE}
            stroke="#FFFFFF"
            strokeWidth="2"
          />
        </g>
      )}
    </svg>
  );
}

export function NdviTrajectory({
  trajectory,
  windowLabel,
  variant = 'insight',
  cycles,
}: {
  trajectory: ReportNdviTrajectory | null | undefined;
  windowLabel?: string;
  /** Overview: one clean curve. Insight: provenance, gaps, and table. */
  variant?: 'overview' | 'insight';
  cycles?: CropCycle[] | null;
}) {
  const overview = variant === 'overview';
  const W = 720;
  const H = overview ? 300 : 220;
  const PAD = { top: overview ? 22 : 12, right: 12, bottom: 26, left: 34 };
  const [hover, setHover] = useState<Pt | null>(null);

  const model = useMemo(() => {
    if (!trajectory?.dates?.length) return null;
    const { dates, ndvi, signal_source } = trajectory;
    const filled = fillInteriorGaps(
      ndvi.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null))
    );

    const x = linearScale([0, Math.max(1, dates.length - 1)], [PAD.left, W - PAD.right]);
    const present = ndvi.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    const hi = present.length ? Math.max(...present) : 1;
    const y = linearScale(
      [0, Math.max(0.4, Math.ceil(hi * 10 + 1) / 10)],
      [H - PAD.bottom, PAD.top]
    );

    const points: Pt[] = dates.map((d, i) => {
      const v = ndvi[i];
      const value = typeof v === 'number' && Number.isFinite(v) ? v : null;
      const display = filled[i];
      const src = (signal_source?.[i] as string | undefined) ?? null;
      return {
        i,
        date: String(d),
        value,
        display,
        observed: value != null,
        source: src,
        x: x(i),
        y: value == null ? null : y(value),
        displayY: display == null ? null : y(display),
      };
    });

    const runs: { source: string; pts: { x: number; y: number; i: number }[] }[] = [];
    for (const p of points) {
      if (p.y == null) continue;
      const src = p.source ?? 'optical';
      const last = runs[runs.length - 1];
      if (last && last.source === src && last.pts[last.pts.length - 1].i === p.i - 1) {
        last.pts.push({ x: p.x, y: p.y, i: p.i });
      } else {
        const bridge =
          last && last.pts[last.pts.length - 1].i === p.i - 1
            ? [last.pts[last.pts.length - 1]]
            : [];
        runs.push({ source: src, pts: [...bridge, { x: p.x, y: p.y, i: p.i }] });
      }
    }

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

    return { x, y, points, runs, gaps, usedSources, baselineY: H - PAD.bottom, dates };
  }, [trajectory, H]);

  if (!model) {
    if (overview) {
      return (
        <div className="bg-white rounded-xl border border-rule p-4 h-full flex flex-col">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
            Vegetation
          </p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5">Vegetation trajectory</h2>
          <p className="text-xs text-stone-500 mt-3 leading-relaxed">
            No observation series was stored for this assessment. A re-run will populate it.
          </p>
        </div>
      );
    }
    return (
      <ChartFrame
        title="Vegetation trajectory"
        subtitle="NDVI measured over this parcel."
        empty="No observation series was stored for this assessment. Evidence is written on new runs; a re-assessment will populate it."
      />
    );
  }

  const { x, y, points, runs, gaps, usedSources, baselineY, dates } = model;
  const gradId = defsId('ndvi-fill', `${variant}-${trajectory!.dates[0] ?? '0'}`);
  const hatchId = defsId('ndvi-gap', `${variant}-${trajectory!.dates[0] ?? '0'}`);
  const observedCount = points.filter(
    (p) => p.value != null && OBSERVED.includes((p.source ?? 'optical') as SignalSource)
  ).length;
  const yTicks = ticks(y.domain, 4);
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));
  const displayCycles = resolveCropCycles(cycles, trajectory);

  const cycleBands: { x0: number; x1: number; label: string; color: string }[] = [];
  for (let i = 0; i < displayCycles.length; i++) {
    const c = displayCycles[i];
    const startIso =
      c.phenology?.sos ||
      c.sowing_date ||
      (typeof c.start_date === 'string' ? c.start_date : null);
    const endIso =
      c.phenology?.eos ||
      c.harvest_date ||
      (typeof c.end_date === 'string' ? c.end_date : null);
    const start = xAtDate(startIso, dates, x);
    const end = xAtDate(endIso, dates, x);
    if (start == null || end == null) continue;
    cycleBands.push({
      x0: Math.min(start, end),
      x1: Math.max(start, end),
      label: cycleTitle(c, i),
      color: SERIES[i % SERIES.length],
    });
  }

  const chart = (
    <NdviChartSvg
      points={points}
      runs={runs}
      gaps={gaps}
      y={y}
      x={x}
      yTicks={yTicks}
      pad={PAD}
      width={W}
      height={H}
      baselineY={baselineY}
      gradId={gradId}
      hatchId={hatchId}
      labelEvery={labelEvery}
      overview={overview}
      cycleBands={cycleBands}
      hover={hover}
      onHover={setHover}
    />
  );

  const hoverNdvi = hover?.value ?? hover?.display ?? null;
  const hoverHint = hover ? (
    <p className="text-[12px] text-stone-700 mt-2 font-medium tabular-nums min-h-[1.25rem]">
      {tooltipDate(hover.date)}
      {hoverNdvi != null
        ? ` · NDVI ${hoverNdvi.toFixed(3)}${hover.value == null ? ' (estimated)' : ''}`
        : ' · no observation'}
      {hover.source && hover.value != null
        ? ` · ${SOURCE_STYLE[hover.source]?.label ?? hover.source}`
        : ''}
    </p>
  ) : (
    <p className="text-[12px] text-ink-muted mt-2 min-h-[1.25rem]">
      Hover the curve to read a date and NDVI
      {cycleBands.length ? ` · ${cycleBands.length} growing season${cycleBands.length === 1 ? '' : 's'} marked` : ''}.
    </p>
  );

  if (overview) {
    return (
      <div className="bg-white rounded-xl border border-rule p-4 h-full flex flex-col">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
          Vegetation
        </p>
        <h2 className="text-sm font-bold text-stone-900 mt-0.5">Vegetation trajectory</h2>
        <p className="text-xs text-stone-500 mt-0.5 mb-2">
          NDVI over this parcel{windowLabel ? ` · ${windowLabel}` : ''}. Higher is more vigorous
          green cover. Shaded spans are detected growing seasons.
        </p>
        <div className="flex-1 min-h-[240px]">{chart}</div>
        {hoverHint}
        {cycleBands.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {cycleBands.map((b) => (
              <span
                key={b.label}
                className="text-[10px] font-semibold px-2 py-0.5 rounded-full border"
                style={{ color: b.color, borderColor: b.color }}
              >
                {b.label}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

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
          Nothing is drawn across a bin with no observation — a line through a gap would assert a
          measurement that was never taken.
          {trajectory!.comparison_available === false && trajectory!.comparison_note && (
            <> {trajectory!.comparison_note}</>
          )}
        </>
      }
    >
      {chart}
    </ChartFrame>
  );
}
