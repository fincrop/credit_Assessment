/** Formatting utilities — migrated from frontend/src/utils/format.ts */

export function formatNumber(n: unknown, digits = 1): string {
  if (n === null || n === undefined || n === '') return '—';
  const x = typeof n === 'number' ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return '—';
  return x.toFixed(digits);
}

export function formatPct(n: unknown): string {
  if (n === null || n === undefined) return '—';
  if (typeof n === 'string' && n.includes('%')) return n;
  const x = typeof n === 'number' ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return String(n);
  return `${x.toFixed(1)}%`;
}

export function humanizeKey(key: string): string {
  const map: Record<string, string> = {
    crop_detection: 'Crop detection',
    crop_performance: 'Crop performance',
    yield_potential: 'Yield proxy',
    weather_safety: 'Weather safety',
    anomaly_penalty: 'Anomaly / stress',
    cropping_intensity: 'Cycles per year',
    govt_benefits: 'Govt. benefits',
    cycles_per_year: 'Cycles per year',
    land_utilization_fraction: 'Land utilization',
  };
  return (
    map[key] ||
    key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/*
 * `riskColor` / `riskBgClass` / `formatRupees` were removed here.
 *
 * The first two were a third and fourth score→colour ramp that disagreed with
 * the KBS bands and with each other. Risk categories now resolve through
 * `bandForRiskCategory` + `bandChipStyle` in `lib/kbsScore.ts`, so a risk pill
 * and the gauge can never show different colours for the same verdict.
 * `formatRupees` had no callers — the pipeline emits no ₹ figures by design.
 */
