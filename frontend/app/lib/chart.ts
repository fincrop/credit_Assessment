/**
 * Chart primitives.
 *
 * Deliberately not a charting library. `package.json` carries no chart
 * dependency and that is the right call for this product: the charts here are
 * few and bespoke, hand-rolled SVG server-renders and prints at full
 * resolution, and a d3-based library would fight React 19 over refs while
 * adding 60–90 kB for features none of these charts use.
 *
 * What belongs here: scales, path builders, tick selection, and the frame
 * every chart wraps in. What does NOT belong here: anything that knows what
 * NDVI or a sub-index is.
 */

export interface Scale {
  (value: number): number;
  domain: [number, number];
  range: [number, number];
  /** Inverse — pixel back to data. For crosshairs. */
  invert(px: number): number;
}

export function linearScale(
  domain: [number, number],
  range: [number, number]
): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const fn = ((v: number) => r0 + ((v - d0) / span) * (r1 - r0)) as Scale;
  fn.domain = domain;
  fn.range = range;
  fn.invert = (px: number) => d0 + ((px - r0) / (r1 - r0 || 1)) * span;
  return fn;
}

/**
 * A polyline path that BREAKS at nulls rather than bridging them.
 *
 * This is the whole point. A line drawn through a fortnight with no
 * observation asserts a measurement that was never taken, which is precisely
 * the fabrication the pipeline spent v6 eliminating. Gaps stay gaps.
 */
export function linePath(
  points: { x: number; y: number | null }[]
): string {
  let d = '';
  let pen = false;
  for (const p of points) {
    if (p.y == null || !Number.isFinite(p.y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)} `;
    pen = true;
  }
  return d.trim();
}

/** Contiguous runs of non-null points, for area fills and per-segment styling. */
export function segments<T extends { y: number | null }>(points: T[]): T[][] {
  const out: T[][] = [];
  let run: T[] = [];
  for (const p of points) {
    if (p.y == null || !Number.isFinite(p.y)) {
      if (run.length) out.push(run);
      run = [];
    } else {
      run.push(p);
    }
  }
  if (run.length) out.push(run);
  return out;
}

/** Area path under a run of points, closed to a baseline. */
export function areaPath(
  points: { x: number; y: number }[],
  baselineY: number
): string {
  if (points.length === 0) return '';
  const top = points.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');
  const first = points[0];
  const last = points[points.length - 1];
  return `${top} L${last.x.toFixed(2)} ${baselineY.toFixed(2)} L${first.x.toFixed(2)} ${baselineY.toFixed(2)} Z`;
}

/**
 * Round tick values covering a domain. Aims for `count` ticks, lands on 1/2/5
 * multiples so the labels read as numbers a person would choose.
 */
export function ticks(domain: [number, number], count = 5): number[] {
  const [d0, d1] = domain;
  const span = d1 - d0;
  if (!Number.isFinite(span) || span === 0) return [d0];
  const rough = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  // Accumulating `v += step` in floating point yields values like
  // 0.6000000000000001, which reach the axis as labels unless every caller
  // remembers to format them. Index from the start and round to the step's
  // own precision instead, so the helper returns the numbers it promises.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  const start = Math.ceil(d0 / step) * step;
  const out: number[] = [];
  for (let i = 0; ; i++) {
    const v = Number((start + i * step).toFixed(decimals));
    if (v > d1 + step * 1e-6) break;
    out.push(v);
  }
  return out;
}

/** Stable id for SVG defs. Collisions between two charts on one page break fills. */
export function defsId(prefix: string, key: string): string {
  return `${prefix}-${key.replace(/[^a-zA-Z0-9_-]/g, '')}`;
}
