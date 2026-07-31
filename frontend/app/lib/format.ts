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

export function formatRupees(n: number | undefined | null): string {
  if (!n) return '₹—';
  return `₹${n.toLocaleString('en-IN')}`;
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

export function riskColor(risk: string | undefined): string {
  const r = (risk || '').toUpperCase();
  if (r === 'LOW') return 'text-emerald-400';
  if (r === 'MEDIUM') return 'text-amber-400';
  if (r === 'HIGH') return 'text-orange-400';
  if (r.includes('VERY')) return 'text-red-400';
  return 'text-stone-500';
}

export function riskBgClass(risk: string | undefined): string {
  const r = (risk || '').toUpperCase();
  if (r === 'LOW') return 'bg-emerald-50 text-emerald-800 border-emerald-200';
  if (r === 'MEDIUM') return 'bg-amber-50 text-amber-900 border-amber-200';
  if (r === 'HIGH') return 'bg-orange-50 text-orange-800 border-orange-200';
  if (r.includes('VERY')) return 'bg-red-50 text-red-800 border-red-200';
  return 'bg-stone-100 text-stone-600 border-stone-200';
}
