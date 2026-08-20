/**
 * Growing seasons inferred from the NDVI series the dashboard already plots.
 *
 * Used when the stored assessment has no cycle rows (older jobs, or CVI
 * gating missed NDVI ~0.45–0.55 crops). Mirrors the backend NDVI-anchored
 * fallback so the chart and the seasons panel agree.
 */

import type { CropCycle } from '../types/assessment';
import type { ReportNdviTrajectory } from '../types/report';

const PEAK_FLOOR = 0.38;
const MIN_PROM = 0.08;
const MIN_RISE = 0.08;
const MIN_DAYS = 40;
const MAX_DAYS = 240;

function filled(values: (number | null | undefined)[]): (number | null)[] {
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

function seasonFromPeak(iso: string): { season_type: string; season_label: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { season_type: 'unknown', season_label: 'Growing season' };
  const m = d.getMonth() + 1;
  const y = d.getFullYear();
  if (m >= 6 && m <= 10) return { season_type: 'kharif', season_label: `Kharif ${y}` };
  if (m >= 11 || m <= 3) {
    const rabiYear = m <= 3 ? y : y + 1;
    return { season_type: 'rabi', season_label: `Rabi ${rabiYear}` };
  }
  return { season_type: 'zaid', season_label: `Zaid ${y}` };
}

function dayIndex(iso: string, origin: string): number {
  const a = new Date(origin).getTime();
  const b = new Date(iso).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b)) return 0;
  return Math.round((b - a) / 86400000);
}

function medianStepDays(dates: string[]): number {
  const steps: number[] = [];
  for (let i = 1; i < dates.length; i++) {
    const d = dayIndex(String(dates[i]), String(dates[i - 1]));
    if (d > 0 && d < 45) steps.push(d);
  }
  if (!steps.length) return 10;
  steps.sort((a, b) => a - b);
  return steps[Math.floor(steps.length / 2)] || 10;
}

function trough(
  signal: (number | null)[],
  peak: number,
  lo: number,
  hi: number,
  toward: 'left' | 'right',
  win: number,
  half: number
): number {
  const a = toward === 'left' ? Math.max(lo, peak - half) : Math.min(signal.length - 1, peak + win);
  const b = toward === 'left' ? Math.max(a, peak - win) : Math.min(hi, peak + half);
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  let vmin = Infinity;
  for (let i = start; i <= end; i++) {
    const v = signal[i];
    if (v != null && v < vmin) vmin = v;
  }
  if (!Number.isFinite(vmin)) return toward === 'left' ? a : b;
  const slack = 0.04;
  if (toward === 'left') {
    for (let i = end; i >= start; i--) {
      const v = signal[i];
      if (v != null && v <= vmin + slack) return i;
    }
    return start;
  }
  for (let i = start; i <= end; i++) {
    const v = signal[i];
    if (v != null && v <= vmin + slack) return i;
  }
  return end;
}

function mergePlateauPeaks(peaks: number[], signal: (number | null)[], minDrop: number): number[] {
  if (peaks.length < 2) return peaks;
  const out = [peaks[0]];
  for (let i = 1; i < peaks.length; i++) {
    const a = out[out.length - 1];
    const b = peaks[i];
    let minBetween = Infinity;
    for (let k = a; k <= b; k++) {
      const v = signal[k];
      if (v != null && v < minBetween) minBetween = v;
    }
    const ha = signal[a] ?? 0;
    const hb = signal[b] ?? 0;
    if (Math.min(ha, hb) - minBetween < minDrop) {
      if (hb > ha) out[out.length - 1] = b;
    } else {
      out.push(b);
    }
  }
  return out;
}

export function inferCyclesFromNdvi(
  trajectory: ReportNdviTrajectory | null | undefined
): CropCycle[] {
  const dates = trajectory?.dates;
  const ndvi = trajectory?.ndvi;
  if (!dates?.length || !ndvi?.length) return [];
  const signal = filled(ndvi);
  const n = Math.min(dates.length, signal.length);
  if (n < 12) return [];

  const step = medianStepDays(dates.slice(0, n).map(String));
  const win = Math.max(2, Math.round(21 / step));
  const half = Math.max(win + 1, Math.round(120 / step));
  const minSep = Math.max(win + 1, Math.round(55 / step));

  const peaks: number[] = [];
  for (let i = win; i < n - win; i++) {
    const v = signal[i];
    if (v == null || v < PEAK_FLOOR) continue;
    let localMax = v;
    for (let k = i - win; k <= i + win; k++) {
      const u = signal[k];
      if (u != null && u > localMax) localMax = u;
    }
    if (v < localMax - 0.005) continue;
    let leftMin = v;
    let rightMin = v;
    for (let k = Math.max(0, i - half); k <= i; k++) {
      const u = signal[k];
      if (u != null && u < leftMin) leftMin = u;
    }
    for (let k = i; k <= Math.min(n - 1, i + half); k++) {
      const u = signal[k];
      if (u != null && u < rightMin) rightMin = u;
    }
    if (Math.max(v - leftMin, v - rightMin) < MIN_PROM) continue;
    if (peaks.length && i - peaks[peaks.length - 1] < minSep) {
      const prev = peaks[peaks.length - 1];
      if ((signal[i] ?? 0) > (signal[prev] ?? 0)) peaks[peaks.length - 1] = i;
    } else {
      peaks.push(i);
    }
  }

  const kept = mergePlateauPeaks(peaks, signal, MIN_PROM);

  const cycles: CropCycle[] = [];
  let used = -1;
  for (let k = 0; k < kept.length; k++) {
    const peak = kept[k];
    if (peak <= used) continue;
    const hi = k + 1 < kept.length ? kept[k + 1] - 1 : n - 1;
    const sow = trough(signal, peak, used + 1, peak, 'left', win, half);
    const harv = trough(signal, peak, peak, hi, 'right', win, half);
    const duration = dayIndex(String(dates[harv]), String(dates[sow]));
    const peakV = signal[peak];
    const base = signal[sow];
    if (peakV == null || base == null || peakV - base < MIN_RISE) continue;
    if (duration < MIN_DAYS || duration > MAX_DAYS) continue;
    const peakIso = String(dates[peak]);
    const season = seasonFromPeak(peakIso);
    cycles.push({
      sowing_date: String(dates[sow]).slice(0, 10),
      harvest_date: String(dates[harv]).slice(0, 10),
      peak_date: peakIso.slice(0, 10),
      duration_days: duration,
      peak_ndvi: peakV,
      season_type: season.season_type,
      season_label: season.season_label,
      phenology: {
        sos: String(dates[sow]).slice(0, 10),
        pos: peakIso.slice(0, 10),
        eos: String(dates[harv]).slice(0, 10),
        fit_ok: false,
        reason: 'ndvi_series',
      },
    });
    used = harv;
  }
  return cycles;
}

export function cyclesHaveDates(cycles: CropCycle[] | null | undefined): boolean {
  return (cycles || []).some((c) => !!(c.sowing_date || c.phenology?.sos || c.harvest_date));
}

/**
 * Prefer stored cycles when they already describe the seasons. Otherwise use
 * the NDVI series — including when the stored job under-counted modest
 * (NDVI ~0.5) crops that are clearly on the chart.
 */
export function resolveCropCycles(
  stored: CropCycle[] | null | undefined,
  trajectory: ReportNdviTrajectory | null | undefined
): CropCycle[] {
  const inferred = inferCyclesFromNdvi(trajectory);
  const dated = cyclesHaveDates(stored) ? stored || [] : [];
  if (inferred.length > dated.length) return inferred;
  if (dated.length) return dated;
  return inferred;
}
