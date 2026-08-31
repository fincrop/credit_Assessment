import type { CropCycle, SeasonPerformance } from '../types/assessment';

export function yearOfCycle(cycle: CropCycle): number | null {
  const fromLabel = String(cycle.season_label || '').match(/20\d{2}/)?.[0];
  const fromSos = cycle.phenology?.sos?.slice(0, 4);
  const fromSow = cycle.sowing_date?.slice(0, 4);
  const y = Number(fromLabel || fromSos || fromSow);
  return Number.isFinite(y) ? y : null;
}

/** Same titles as the Crops & seasons tab — prefer cycle season_label. */
export function prettySeasonTitle(
  cycle: CropCycle | undefined,
  season: SeasonPerformance | undefined,
  index: number
): string {
  const fromCycle = cycle?.season_label || cycle?.season_type;
  if (fromCycle) return String(fromCycle).replace(/_/g, ' ');
  const s = String(season?.season || '').trim();
  const upper = s.toUpperCase();
  const cycleMatch = upper.match(/^CYCLE[_\s-]?(\d+)$/);
  if (cycleMatch) {
    return season?.year != null ? `Season ${cycleMatch[1]} · ${season.year}` : `Season ${cycleMatch[1]}`;
  }
  if (upper === 'KHARIF') return season?.year != null ? `Kharif ${season.year}` : 'Kharif';
  if (upper === 'RABI') return season?.year != null ? `Rabi ${season.year}` : 'Rabi';
  if (s) return season?.year != null ? `${s.replace(/_/g, ' ')} ${season.year}` : s.replace(/_/g, ' ');
  return `Season ${index + 1}`;
}
