/**
 * Client functions for the Assessment Dashboard.
 *
 * All calls go through internal Next.js API routes (no CORS issues).
 * The Next.js routes proxy to FastAPI and MongoDB as needed.
 */

import type { AssessmentJob } from '../types/assessment';

/** Enqueue a new assessment job via MongoDB job queue. Returns job_id immediately. */
export async function runAssessmentJob(params: {
  farmerId: string;
  /** Tri-state: null = unknown (never coerce with !!) */
  pmKisanEnrolled?: boolean | null;
  hasCropInsurance?: boolean | null;
}): Promise<{ job_id: string; status: string }> {
  const res = await fetch('/api/assess/enqueue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      farmer_id: params.farmerId.trim(),
      pm_kisan_enrolled:
        params.pmKisanEnrolled === undefined ? null : params.pmKisanEnrolled,
      has_crop_insurance:
        params.hasCropInsurance === undefined ? null : params.hasCropInsurance,
    }),
  });

  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Invalid response (${res.status}): ${text.slice(0, 200)}`);
  }

  if (!res.ok) {
    const d = data as Record<string, unknown>;
    throw new Error(String(d?.error ?? d?.detail ?? `HTTP ${res.status}`));
  }

  return data as { job_id: string; status: string };
}

export type PollJobResult =
  | { ok: true; job: AssessmentJob }
  | { ok: false; transient: boolean; message: string; status?: number };

/**
 * Poll job status. Never throws on HTTP errors — returns { ok:false } so the
 * dashboard can backoff without spamming the console on DNS blips.
 */
export async function pollJobStatusSafe(jobId: string): Promise<PollJobResult> {
  const id = jobId.trim();
  if (!/^[a-f0-9]{24}$/i.test(id)) {
    return { ok: false, transient: false, message: 'Invalid job id' };
  }

  try {
    const res = await fetch(`/api/assess/status/${encodeURIComponent(id)}`, {
      credentials: 'include',
      cache: 'no-store',
    });

    const text = await res.text();
    let data: Record<string, unknown> = {};
    try {
      data = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      return {
        ok: false,
        transient: true,
        message: `Poll response not JSON (${res.status})`,
        status: res.status,
      };
    }

    if (!res.ok) {
      const transient = data.transient === true || res.status === 503;
      return {
        ok: false,
        transient,
        message: String(data.error ?? `HTTP ${res.status}`),
        status: res.status,
      };
    }

    return { ok: true, job: data as unknown as AssessmentJob };
  } catch (err) {
    return {
      ok: false,
      transient: true,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

/** @deprecated Prefer pollJobStatusSafe — kept for callers that expect throws. */
export async function pollJobStatus(jobId: string): Promise<AssessmentJob> {
  const result = await pollJobStatusSafe(jobId);
  if (!result.ok) {
    const err = new Error(result.message) as Error & { transient?: boolean };
    err.transient = result.transient;
    throw err;
  }
  return result.job;
}

/** Ingest a raw Agristack API response into MongoDB farm_info. */
export async function ingestFarmerData(agristackResponse: unknown): Promise<{
  success: boolean;
  farmer_ids: string[];
  message: string;
  created?: string[];
  updated?: string[];
}> {
  const res = await fetch('/api/ingest-farmer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ agristack_response: agristackResponse }),
  });

  const data = await res.json();
  if (!res.ok) throw new Error(data?.error ?? `HTTP ${res.status}`);
  return data;
}
