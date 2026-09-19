/**
 * Shared helpers for the crop-classification job routes.
 *
 * Jobs live in their own `classification_jobs` collection rather than the
 * `jobs` collection the credit assessment uses. The two have different stage
 * vocabularies, different payload shapes and different ownership rules (a
 * classification job is owned by whoever drew the area; an assessment job is
 * owned through the farmer record), and merging them would mean every reader
 * of either had to branch on a discriminator.
 */

import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';

export const TARGET_DB =
  process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

export const JOBS_COLLECTION = 'classification_jobs';

export interface AuthedUser {
  id: string;
  email: string;
}

/** Resolve the caller, or the 401 to return. Never both. */
export async function requireUser(
  req: NextRequest
): Promise<{ user: AuthedUser } | { response: NextResponse }> {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) {
    return { response: NextResponse.json({ error: 'Unauthorized' }, { status: 401 }) };
  }
  const payload = await verifyJWT(token);
  if (!payload?.email) {
    return { response: NextResponse.json({ error: 'Unauthorized' }, { status: 401 }) };
  }
  return { user: { id: String(payload.id ?? ''), email: String(payload.email) } };
}

export async function jobsCollection() {
  const { client } = await connectToDatabase();
  return client.db(TARGET_DB).collection(JOBS_COLLECTION);
}

/** Base URL of the FastAPI service, or null when it is not configured. */
export function pipelineBaseUrl(): string | null {
  const raw = (process.env.PIPELINE_API_URL || process.env.ASSESSMENT_API_URL || '').trim();
  return raw ? raw.replace(/\/$/, '') : null;
}

export function pipelineHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const key = (process.env.PIPELINE_API_SERVICE_KEY || process.env.API_SERVICE_KEY || '').trim();
  if (key) headers['X-API-Key'] = key;
  return headers;
}

/**
 * A classification job is visible to the account that created it. There is no
 * farmer record to inherit ownership from, so this is the only check.
 */
export function ownedBy(job: Record<string, unknown>, user: AuthedUser): boolean {
  return (
    job.requested_by === user.email ||
    (!!user.id && job.requested_by_user_id === user.id)
  );
}

/** Mongo filter matching `ownedBy`. Used for list queries. */
export function ownedByFilter(user: AuthedUser): Record<string, unknown> {
  if (user.id) {
    return { $or: [{ requested_by: user.email }, { requested_by_user_id: user.id }] };
  }
  return { requested_by: user.email };
}

export function typedRegionName(inputs: unknown): string {
  if (!inputs || typeof inputs !== 'object') return '';
  const n = (inputs as Record<string, unknown>).region_name;
  return typeof n === 'string' ? n.trim() : '';
}

/** The typed region name wins over the drawn-polygon default ("Drawn area 1"). */
export function withTypedRegionName<T extends Record<string, unknown>>(
  result: T,
  inputs: unknown
): T {
  const name = typedRegionName(inputs);
  if (!name) return result;
  return { ...result, aoi_name: name };
}

export function areasWithRegionName<T extends { name?: string }>(
  areas: T[],
  inputs: unknown
): T[] {
  const name = typedRegionName(inputs);
  if (!name) return areas;
  return areas.map((a) => ({ ...a, name }));
}
