/**
 * Client for the Farmer Assessment Report.
 *
 * Like `pollJobStatusSafe`, this never throws on an HTTP error — a report page
 * has four genuinely different empty states and collapsing them into one
 * thrown Error would repeat the mistake the refusal layer just fixed:
 *
 *   not-configured  the pipeline API URL is unset (a deployment problem)
 *   not-found       no assessment stored for this farmer yet
 *   unavailable     upstream is down or the DB read failed — retryable
 *   error           anything else
 */

import type { ReportPayload, ReportResponse, ReportSectionsPresent } from '../types/report';

export type ReportResult =
  | { ok: true; report: ReportPayload }
  | {
      ok: false;
      kind: 'not-found' | 'not-configured' | 'unavailable' | 'error';
      message: string;
      status?: number;
    };

export async function fetchReport(
  farmerId: string,
  opts?: { plotKey?: string; signal?: AbortSignal }
): Promise<ReportResult> {
  const id = String(farmerId || '').trim();
  if (!id) return { ok: false, kind: 'error', message: 'farmer_id is required' };

  const qs = opts?.plotKey ? `?plot_key=${encodeURIComponent(opts.plotKey)}` : '';

  try {
    const res = await fetch(`/api/report/${encodeURIComponent(id)}${qs}`, {
      credentials: 'include',
      cache: 'no-store',
      signal: opts?.signal,
    });

    const text = await res.text();
    let data: Record<string, unknown> = {};
    try {
      data = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      return {
        ok: false,
        kind: 'unavailable',
        message: `Report response was not JSON (${res.status})`,
        status: res.status,
      };
    }

    if (!res.ok) {
      const message = String(data.error ?? `HTTP ${res.status}`);
      const kind =
        res.status === 404
          ? 'not-found'
          : res.status === 503 && /not configured/i.test(message)
            ? 'not-configured'
            : res.status === 503 || res.status === 502
              ? 'unavailable'
              : 'error';
      return { ok: false, kind, message, status: res.status };
    }

    const body = data as unknown as ReportResponse;
    if (!body?.report) {
      return { ok: false, kind: 'error', message: 'Report response had no payload' };
    }

    return { ok: true, report: body.report };
  } catch (err) {
    // An aborted fetch is a navigation, not a failure worth surfacing.
    if (err instanceof DOMException && err.name === 'AbortError') {
      return { ok: false, kind: 'unavailable', message: 'aborted' };
    }
    return {
      ok: false,
      kind: 'unavailable',
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

/**
 * Is a section renderable?
 *
 * Prefer this over truthiness checks on the payload. `sections_present` is the
 * backend's own answer, and it distinguishes an absent section from one that
 * is present but legitimately empty.
 */
export function hasSection(
  report: ReportPayload | null | undefined,
  section: keyof ReportSectionsPresent
): boolean {
  if (!report) return false;
  const present = report.sections_present;
  if (present && section in present) return present[section] === true;

  // Older payloads predate sections_present. Fall back to the field itself
  // rather than assuming absent — an assessment saved last month should still
  // render what it has.
  switch (section) {
    case 'score':
      return report.score?.kbs != null;
    case 'trend':
      return report.trend != null;
    case 'sub_indices':
      return (report.sub_indices?.length ?? 0) > 0;
    case 'ndvi_trajectory':
      return report.ndvi_trajectory != null;
    case 'land_cover':
      return report.land_cover != null;
    case 'crop_verification':
      return report.crop_verification != null;
    case 'narrative':
      return !!report.narrative?.text;
    default:
      return false;
  }
}

/**
 * Trend as a readable phrase, or null.
 *
 * Returns null on a first assessment — the payload deliberately omits `trend`
 * there, and the UI must render NO chip rather than one saying "0" or "—".
 * A zero against a baseline that does not exist invents a history.
 */
export function trendLabel(report: ReportPayload | null | undefined): string | null {
  const t = report?.trend;
  if (!t || typeof t.delta !== 'number') return null;
  if (t.direction === 'flat') return `Unchanged since ${t.previous_date?.slice(0, 10) ?? 'last assessment'}`;
  const sign = t.delta > 0 ? '+' : '';
  return `${sign}${t.delta} since ${t.previous_date?.slice(0, 10) ?? 'last assessment'}`;
}
