import type { FarmAssessment } from '../types/assessment';
import { plotKeyOf } from './plotKey';
import type { StreamFarmRow, StreamRowStatus } from '../dashboard/components/StreamingFarmList';

export function rowStatusFromAssessment(a: FarmAssessment | null | undefined): StreamRowStatus {
  if (!a) return 'pending';
  const reason = String(a.skipped_reason || '');
  if (reason.startsWith('error:')) return 'failed';
  if (!a.included || reason) return 'skipped';
  if (a.index_score != null) return 'scored';
  return 'skipped';
}

export function buildStreamRows(opts: {
  seedFarms: Record<string, unknown>[];
  partialAssessments?: FarmAssessment[];
  finalAssessments?: FarmAssessment[];
  jobStatus: string;
  analyzingPlotKey?: string | null;
}): StreamFarmRow[] {
  const byKey = new Map<string, FarmAssessment>();
  for (const a of opts.partialAssessments || []) {
    const k = plotKeyOf(a);
    if (k) byKey.set(k, a);
  }
  for (const a of opts.finalAssessments || []) {
    const k = plotKeyOf(a);
    if (k) byKey.set(k, a);
  }

  const running = opts.jobStatus === 'QUEUED' || opts.jobStatus === 'RUNNING';
  let firstPending = true;

  return (opts.seedFarms || []).map((f) => {
    const key = plotKeyOf(f as { plot_key?: string; farm_id?: string });
    const assessment = byKey.get(key) || null;
    let status = rowStatusFromAssessment(assessment);
    if (!assessment && running) {
      // Mark the next unfinished plot Analyzing as soon as Assess is clicked
      // (QUEUED or RUNNING) — not only after the worker flips to RUNNING.
      if (opts.analyzingPlotKey && key === opts.analyzingPlotKey) {
        status = 'analyzing';
        firstPending = false;
      } else if (firstPending) {
        status = 'analyzing';
        firstPending = false;
      } else {
        status = 'pending';
      }
    }
    return {
      plot_key: key,
      farm_id: String(f.farm_id || key),
      area_ha: typeof f.area_ha === 'number' ? f.area_ha : undefined,
      crop: (f.primary_crop as string) || null,
      is_ror_owner: f.is_ror_owner as boolean | null | undefined,
      tenure_factor: typeof f.tenure_factor === 'number' ? f.tenure_factor : undefined,
      status,
      assessment,
    };
  });
}

export function portfolioSummaryFromRows(rows: StreamFarmRow[]): string {
  const total = rows.length;
  const scored = rows.filter((r) => r.status === 'scored').length;
  const failed = rows.filter((r) => r.status === 'failed').length;
  const leased = rows.filter(
    (r) => r.is_ror_owner === false || (r.tenure_factor != null && r.tenure_factor < 1)
  ).length;
  const excluded = rows.filter(
    (r) =>
      r.status === 'skipped' &&
      String(r.assessment?.skipped_reason || '').includes('excluded')
  ).length;
  const parts = [`${total} plot${total === 1 ? '' : 's'}`, `${scored} scored`];
  if (leased) parts.push(`${leased} leased/joint`);
  if (excluded) parts.push(`${excluded} excluded`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(' · ');
}

export function countersFromAssessments(rows: FarmAssessment[]): {
  n_plots_total: number;
  n_plots_scored: number;
  n_plots_skipped: number;
  n_plots_failed: number;
  n_plots_done: number;
} {
  let scored = 0;
  let failed = 0;
  let skipped = 0;
  for (const a of rows) {
    const s = rowStatusFromAssessment(a);
    if (s === 'scored') scored += 1;
    else if (s === 'failed') failed += 1;
    else skipped += 1;
  }
  return {
    n_plots_total: rows.length,
    n_plots_scored: scored,
    n_plots_skipped: skipped,
    n_plots_failed: failed,
    n_plots_done: scored + skipped + failed,
  };
}
