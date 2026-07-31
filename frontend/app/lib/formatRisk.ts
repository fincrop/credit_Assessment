/**
 * Risk-index formatting helpers — one place for labels, polarity, gate, yield basis.
 */

import type { ReasonPolarity } from '../types/assessment';

export const DEFAULT_SUBINDEX_WEIGHTS: Record<string, number> = {
  landuse: 30,
  vigor: 25,
  stability: 20,
  weather: 25,
};

export const SUBINDEX_LABELS: Record<string, string> = {
  landuse: 'Land use & activity',
  vigor: 'Vigor & yield potential',
  stability: 'Stability & stress',
  weather: 'Weather resilience / exposure',
  data_confidence: 'Data confidence (gate)',
};

export const SUBSTANTIVE_SUBINDEX_KEYS = [
  'landuse',
  'vigor',
  'stability',
  'weather',
] as const;

export function subIndexLabel(key: string): string {
  return SUBINDEX_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatScoreWhole(n: unknown): string {
  if (n === null || n === undefined || n === '') return '—';
  const x = typeof n === 'number' ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return '—';
  return String(Math.round(x));
}

export function formatScoreOne(n: unknown): string {
  if (n === null || n === undefined || n === '') return '—';
  const x = typeof n === 'number' ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return '—';
  return x.toFixed(1);
}

export function formatGateMultiplier(gate: unknown): string {
  if (gate === null || gate === undefined || gate === '') return '—';
  const x = typeof gate === 'number' ? gate : parseFloat(String(gate));
  if (Number.isNaN(x)) return '—';
  // Gate may be 0–1 or 0–100
  const m = x > 1.5 ? x / 100 : x;
  return `×${m.toFixed(2)}`;
}

export function gateAsFraction(gate: unknown): number | null {
  if (gate === null || gate === undefined || gate === '') return null;
  const x = typeof gate === 'number' ? gate : parseFloat(String(gate));
  if (Number.isNaN(x)) return null;
  return x > 1.5 ? Math.min(1, Math.max(0, x / 100)) : Math.min(1, Math.max(0, x));
}

export function polarityIcon(polarity: ReasonPolarity | undefined): string {
  const p = (polarity || '').toLowerCase();
  if (p === 'positive') return '▲';
  if (p === 'negative') return '▼';
  return '•';
}

export function polarityTextClass(polarity: ReasonPolarity | undefined): string {
  const p = (polarity || '').toLowerCase();
  if (p === 'positive') return 'text-emerald-700';
  if (p === 'negative') return 'text-red-600';
  return 'text-amber-700'; // caveat / unknown
}

export function polarityBgClass(polarity: ReasonPolarity | undefined): string {
  const p = (polarity || '').toLowerCase();
  if (p === 'positive') return 'bg-emerald-50 border-emerald-200 text-emerald-800';
  if (p === 'negative') return 'bg-red-50 border-red-200 text-red-800';
  return 'bg-amber-50 border-amber-200 text-amber-900';
}

function parseYieldPct(pct: number | string | undefined): number {
  if (typeof pct === 'number') return pct;
  if (typeof pct === 'string') return parseFloat(pct);
  return NaN;
}

export function yieldBasisLabel(
  basis: string | undefined,
  pct: number | string | undefined
): { kind: 'peer' | 'self' | 'unknown'; text: string } {
  const n = parseYieldPct(pct);

  if (basis === 'peer_nirv') {
    return {
      kind: 'peer',
      text: Number.isFinite(n)
        ? `${Math.round(n)}th percentile (peer cohort)`
        : 'Peer cohort percentile',
    };
  }

  // Never label these as peer percentiles
  if (basis === 'internal_cvi_auc') {
    return {
      kind: 'self',
      text: Number.isFinite(n)
        ? `Yield-potential score (self-calibrated): ${n.toFixed(0)}`
        : 'Yield-potential score (self-calibrated)',
    };
  }
  if (basis === 'crop_curve_ndvi' || basis === 'crop_specific') {
    return {
      kind: 'self',
      text: Number.isFinite(n)
        ? `Yield-potential score (crop curve): ${n.toFixed(0)}`
        : 'Yield-potential score (crop curve)',
    };
  }
  if (
    basis === 'signal_proxy' ||
    basis === 'signal_only' ||
    basis === 'cycle_proxy' ||
    basis === 'signal_based'
  ) {
    return {
      kind: 'self',
      text: Number.isFinite(n)
        ? `Yield-potential score (signal proxy): ${n.toFixed(0)}`
        : 'Yield-potential score (signal proxy)',
    };
  }

  return { kind: 'unknown', text: pct != null ? String(pct) : '—' };
}

/** Normalize weather_indicators keys across backend / legacy aliases. */
export function readWeatherIndicator(
  ind: Record<string, unknown> | null | undefined,
  ...keys: string[]
): number | null {
  if (!ind) return null;
  for (const k of keys) {
    const v = ind[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

export function triStateLabel(v: boolean | null | undefined): string {
  if (v === true) return 'Yes';
  if (v === false) return 'No';
  return 'Unknown';
}

export function resolveWeights(
  payloadWeights?: Record<string, number> | null
): Record<string, number> {
  if (payloadWeights && Object.keys(payloadWeights).length > 0) {
    return { ...payloadWeights };
  }
  return { ...DEFAULT_SUBINDEX_WEIGHTS };
}
