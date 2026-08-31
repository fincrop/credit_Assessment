/**
 * Client functions for the Assessment Dashboard.
 *
 * All calls go through internal Next.js API routes (no CORS issues).
 * The Next.js routes proxy to FastAPI and MongoDB as needed.
 */

import type { AssessmentJob } from '../types/assessment';
import { apiErrorMessage, readJsonBody } from './httpJson';

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

  const parsed = await readJsonBody<{ job_id: string; status: string }>(res);
  if (!parsed.isJson || !parsed.ok) {
    throw new Error(
      apiErrorMessage(parsed, `Assessment enqueue failed`, 'Assessment enqueue')
    );
  }
  if (!parsed.data?.job_id) {
    throw new Error('Assessment enqueue returned no job_id');
  }
  return parsed.data;
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

    const parsed = await readJsonBody<AssessmentJob & Record<string, unknown>>(res);
    if (!parsed.isJson) {
      return {
        ok: false,
        transient: true,
        message: apiErrorMessage(parsed, 'Poll failed', 'Job status poll'),
        status: parsed.status,
      };
    }

    if (!parsed.ok) {
      const data = parsed.data;
      const transient = data?.transient === true || parsed.status === 503;
      return {
        ok: false,
        transient,
        message: apiErrorMessage(parsed, `HTTP ${parsed.status}`, 'Job status poll'),
        status: parsed.status,
      };
    }

    if (!parsed.data) {
      return {
        ok: false,
        transient: true,
        message: 'Job status poll returned empty body',
        status: parsed.status,
      };
    }

    return { ok: true, job: parsed.data as AssessmentJob };
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

  const parsed = await readJsonBody<{
    success: boolean;
    farmer_ids: string[];
    message: string;
    created?: string[];
    updated?: string[];
    error?: string;
  }>(res);
  if (!parsed.isJson || !parsed.ok) {
    throw new Error(apiErrorMessage(parsed, 'Ingest failed', 'Farmer ingest'));
  }
  if (!parsed.data) {
    throw new Error('Ingest returned empty body');
  }
  return parsed.data;
}

export type PrepareFarmerResult = {
  success: boolean;
  skipped_seek: boolean;
  stage: string;
  farmer_id: string;
  farms: Record<string, unknown>[];
  farm_info?: {
    farmer_id: string;
    name?: string | null;
    mobile?: string | null;
    state?: string | null;
    district?: string | null;
    village?: string | null;
    farmer_benefits?: {
      pm_kisan_enrolled?: boolean | null;
      has_crop_insurance?: boolean | null;
    } | null;
  } | null;
};

/** AgriStack token → seek → webhook → farm_info (dashboard Prepare farms). */
export async function prepareFarmerFarms(params: {
  farmerId: string;
  username: string;
  password: string;
  clientId?: string;
}): Promise<PrepareFarmerResult> {
  const res = await fetch('/api/agristack/prepare-farmer', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      farmer_id: params.farmerId.trim(),
      username: params.username,
      password: params.password,
      client_id: params.clientId,
    }),
  });

  const parsed = await readJsonBody<PrepareFarmerResult & { error?: string }>(res);
  if (!parsed.isJson || !parsed.ok) {
    throw new Error(
      apiErrorMessage(parsed, 'Prepare farms failed', 'Prepare farms')
    );
  }
  if (!parsed.data) {
    throw new Error('Prepare farms returned empty body');
  }
  return parsed.data;
}
