/**
 * AgriStack registered area vs the area of the drawn/ingested boundary.
 *
 * Those two figures often describe different parcels. Showing only the
 * registry number hides the mismatch; showing both, highlighted, is the
 * whole point of this helper.
 */

import type { ParcelViability } from '../types/assessment';

export const AREA_RATIO_LO = 0.8;
export const AREA_RATIO_HI = 1.25;
/** Below this, a 10 m pixel is mostly neighbouring land. */
export const TINY_PLOT_HA = 0.05;

export type AreaMismatch = {
  registeredHa: number;
  measuredHa: number;
  ratio: number;
  /** Mapped plot is far smaller than the registry claim. */
  measuredMuchSmaller: boolean;
};

function finitePositive(n: unknown): number | null {
  const x = typeof n === 'number' ? n : Number(n);
  return Number.isFinite(x) && x > 0 ? x : null;
}

/** Spherical polygon area in hectares from a GeoJSON ring [lng, lat][]. */
export function ringAreaHa(ring: number[][] | null | undefined): number | null {
  if (!ring || ring.length < 3) return null;
  const R = 6371000;
  let area = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[i + 1];
    if (![lng1, lat1, lng2, lat2].every((v) => Number.isFinite(v))) return null;
    area +=
      ((lng2 * Math.PI) / 180 - (lng1 * Math.PI) / 180) *
      (2 + Math.sin((lat1 * Math.PI) / 180) + Math.sin((lat2 * Math.PI) / 180));
  }
  const ha = Math.abs((area * R * R) / 2) / 10000;
  return ha > 0 ? ha : null;
}

export function geometryAreaHa(geom: unknown): number | null {
  if (!geom || typeof geom !== 'object') return null;
  const g = geom as { type?: string; coordinates?: unknown };
  try {
    if (g.type === 'Polygon' && Array.isArray(g.coordinates)) {
      return ringAreaHa((g.coordinates as number[][][])[0]);
    }
    if (g.type === 'MultiPolygon' && Array.isArray(g.coordinates)) {
      let sum = 0;
      let any = false;
      for (const poly of g.coordinates as number[][][][]) {
        const a = ringAreaHa(poly?.[0]);
        if (a != null) {
          sum += a;
          any = true;
        }
      }
      return any ? sum : null;
    }
  } catch {
    return null;
  }
  return null;
}

export function areaMismatchOf(opts: {
  registeredHa?: number | null;
  measuredHa?: number | null;
  viability?: ParcelViability | null;
}): AreaMismatch | null {
  const ev = opts.viability?.evidence;
  const registered =
    finitePositive(ev?.registered_ha) ?? finitePositive(opts.registeredHa);
  const measured =
    finitePositive(ev?.geometry_ha) ?? finitePositive(opts.measuredHa);
  if (registered == null || measured == null) return null;
  const ratio = measured / registered;
  const disagree =
    ev?.areas_disagree === true || ratio < AREA_RATIO_LO || ratio > AREA_RATIO_HI;
  if (!disagree) return null;
  return {
    registeredHa: registered,
    measuredHa: measured,
    ratio,
    measuredMuchSmaller: measured < registered * AREA_RATIO_LO,
  };
}

export function formatHa(n: number, digits = 2): string {
  if (n < 0.01) return `${n.toFixed(3)} ha`;
  return `${n.toFixed(digits)} ha`;
}

/** One-line explanation for lists and banners. */
export function areaMismatchHeadline(m: AreaMismatch): string {
  if (m.measuredMuchSmaller) {
    return `AgriStack lists ${formatHa(m.registeredHa)} but the mapped boundary is only ${formatHa(m.measuredHa)} — the two do not describe the same plot.`;
  }
  return `AgriStack lists ${formatHa(m.registeredHa)} but the mapped boundary is ${formatHa(m.measuredHa)} — those areas do not match.`;
}

export function areaMismatchSkipNote(m: AreaMismatch): string {
  if (m.measuredHa < TINY_PLOT_HA) {
    return `${areaMismatchHeadline(m)} The mapped plot is too small to score honestly.`;
  }
  return areaMismatchHeadline(m);
}
