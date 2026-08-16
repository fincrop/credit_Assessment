import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase, isMongoTransientError } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { isFarmerOwnedBy } from '../../../lib/ownerScope';

/**
 * Report data for a farmer's most recent assessment.
 *
 * Proxies `GET /v1/report/{farmer_id}` on the pipeline API. Two reasons this
 * is a proxy rather than a direct client call:
 *
 *   1. The service key must never reach the browser.
 *   2. The pipeline API authenticates the SERVICE, not the user. Ownership is
 *      the dashboard's job, and it is enforced here before the upstream call —
 *      otherwise any authenticated user could read any farmer's report by
 *      changing the id in the URL.
 *
 * The upstream endpoint is read-only and never triggers a run, so a report can
 * never show a different number from the assessment it claims to describe.
 */

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

function pipelineBaseUrl(): string | null {
  const raw = (process.env.PIPELINE_API_URL || process.env.ASSESSMENT_API_URL || '').trim();
  return raw ? raw.replace(/\/$/, '') : null;
}

function pipelineApiKey(): string | undefined {
  const k = (
    process.env.PIPELINE_API_SERVICE_KEY ||
    process.env.API_SERVICE_KEY ||
    ''
  ).trim();
  return k || undefined;
}

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ farmer_id: string }> }
) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const user = await verifyJWT(token);
    if (!user?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

    const { farmer_id: raw } = await context.params;
    const farmerId = decodeURIComponent(String(raw || '')).trim();
    if (!farmerId) {
      return NextResponse.json({ error: 'farmer_id is required' }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);

    const owned = await isFarmerOwnedBy(farmerId, user, db);
    if (!owned) {
      // 404, not 403 — a 403 confirms the farmer exists, which is itself a
      // disclosure to someone who should not be asking.
      return NextResponse.json({ error: 'No report for this farmer' }, { status: 404 });
    }

    const base = pipelineBaseUrl();
    if (!base) {
      return NextResponse.json(
        {
          error:
            'Report service is not configured (set PIPELINE_API_URL). Reports are served by the pipeline API, not the dashboard database.',
        },
        { status: 503 }
      );
    }

    const plotKey = req.nextUrl.searchParams.get('plot_key');
    const url = new URL(`${base}/v1/report/${encodeURIComponent(farmerId)}`);
    if (plotKey) url.searchParams.set('plot_key', plotKey);

    const key = pipelineApiKey();
    const upstream = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        ...(key ? { 'X-API-Key': key } : {}),
      },
      cache: 'no-store',
    });

    const text = await upstream.text();
    let data: unknown;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      return NextResponse.json(
        { error: `Report service returned non-JSON (${upstream.status})` },
        { status: 502 }
      );
    }

    if (!upstream.ok) {
      const d = data as Record<string, unknown>;
      return NextResponse.json(
        { error: String(d?.detail ?? d?.error ?? `HTTP ${upstream.status}`) },
        // 503 upstream means "report unavailable", which the client may retry.
        { status: upstream.status === 404 ? 404 : upstream.status === 503 ? 503 : 502 }
      );
    }

    return NextResponse.json(data);
  } catch (error) {
    const transient = isMongoTransientError(error);
    if (!transient) console.error('Report API Error:', error);
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : 'Internal server error',
        transient,
      },
      { status: transient ? 503 : 500 }
    );
  }
}
