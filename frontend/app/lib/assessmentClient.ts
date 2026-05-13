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
  pmKisanEnrolled?: boolean;
  hasCropInsurance?: boolean;
}): Promise<{ job_id: string; status: string }> {
  const res = await fetch('/api/assess/enqueue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      farmer_id: params.farmerId.trim(),
      pm_kisan_enrolled: params.pmKisanEnrolled ?? false,
      has_crop_insurance: params.hasCropInsurance ?? false,
    }),
  });

  const text = await res.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : {}; } catch {
    throw new Error(`Invalid response (${res.status}): ${text.slice(0, 200)}`);
  }

  if (!res.ok) {
    const d = data as Record<string, unknown>;
    throw new Error(String(d?.error ?? d?.detail ?? `HTTP ${res.status}`));
  }

  return data as { job_id: string; status: string };
}

/** Poll job status from MongoDB via Next.js API route. */
export async function pollJobStatus(jobId: string): Promise<AssessmentJob> {
  const id = jobId.trim();
  if (!/^[a-f0-9]{24}$/i.test(id)) {
    throw new Error('Invalid job id; submit a new assessment from the dashboard.');
  }
  const res = await fetch(`/api/assess/status/${encodeURIComponent(id)}`);

  const text = await res.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : {}; } catch {
    throw new Error(`Poll response not JSON (${res.status}): ${text.slice(0, 100)}`);
  }

  if (!res.ok) {
    const d = data as Record<string, unknown>;
    throw new Error(String(d?.error ?? `HTTP ${res.status}`));
  }

  return data as AssessmentJob;
}

/** Ingest a raw Agristack API response into MongoDB farm_info. */
export async function ingestFarmerData(agristackResponse: unknown): Promise<{
  success: boolean;
  farmer_ids: string[];
  message: string;
}> {
  const res = await fetch('/api/ingest-farmer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agristack_response: agristackResponse }),
  });

  const data = await res.json();
  if (!res.ok) throw new Error(data?.error ?? `HTTP ${res.status}`);
  return data;
}
