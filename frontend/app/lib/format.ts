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
    yield_potential: 'Yield potential',
    weather_safety: 'Weather safety',
    anomaly_penalty: 'Anomaly / stress',
    cropping_intensity: 'Cropping intensity',
    govt_benefits: 'Govt. benefits',
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
  return 'text-gray-400';
}

export function riskBgClass(risk: string | undefined): string {
  const r = (risk || '').toUpperCase();
  if (r === 'LOW') return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25';
  if (r === 'MEDIUM') return 'bg-amber-500/15 text-amber-400 border-amber-500/25';
  if (r === 'HIGH') return 'bg-orange-500/15 text-orange-400 border-orange-500/25';
  if (r.includes('VERY')) return 'bg-red-500/15 text-red-400 border-red-500/25';
  return 'bg-gray-700/50 text-gray-400 border-gray-600';
}
