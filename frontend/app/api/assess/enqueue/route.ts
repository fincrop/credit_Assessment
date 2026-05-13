import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/** Base URL of the FastAPI service (no trailing slash). When set, jobs run in-process on the API (no separate worker). */
function pipelineBaseUrl(): string | null {
  const raw = (
    process.env.PIPELINE_API_URL ||
    process.env.ASSESSMENT_API_URL ||
    ''
  ).trim();
  if (!raw) return null;
  return raw.replace(/\/$/, '');
}

function pipelineApiKey(): string | undefined {
  const k = (
    process.env.PIPELINE_API_SERVICE_KEY ||
    process.env.API_SERVICE_KEY ||
    ''
  ).trim();
  return k || undefined;
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { farmer_id, pm_kisan_enrolled, has_crop_insurance } = body;

    if (!farmer_id) {
      return NextResponse.json({ error: 'farmer_id is required' }, { status: 400 });
    }

    const base = pipelineBaseUrl();
    if (base) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const key = pipelineApiKey();
      if (key) headers['X-API-Key'] = key;

      const res = await fetch(`${base}/v1/jobs/assess`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          farmer_id: String(farmer_id).trim(),
          pm_kisan_enrolled: !!pm_kisan_enrolled,
          has_crop_insurance: !!has_crop_insurance,
        }),
      });

      const text = await res.text();
      let data: unknown;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        return NextResponse.json(
          { error: `Pipeline API invalid JSON (${res.status}): ${text.slice(0, 200)}` },
          { status: 502 }
        );
      }

      if (!res.ok) {
        const d = data as Record<string, unknown>;
        return NextResponse.json(
          { error: String(d?.detail ?? d?.error ?? `Pipeline API HTTP ${res.status}`) },
          { status: res.status >= 400 && res.status < 600 ? res.status : 502 }
        );
      }

      return NextResponse.json(data);
    }

    // Legacy: enqueue in Mongo only — requires `python worker.py` (or another consumer).
    console.warn(
      '[assess/enqueue] PIPELINE_API_URL is not set; job will stay QUEUED until a worker processes it.'
    );

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const jobsCollection = db.collection('jobs');

    const jobDoc = {
      farmer_id,
      pm_kisan_enrolled: !!pm_kisan_enrolled,
      has_crop_insurance: !!has_crop_insurance,
      status: 'QUEUED',
      created_at: new Date(),
      updated_at: new Date(),
      result: null,
      error: null,
    };

    const result = await jobsCollection.insertOne(jobDoc);

    return NextResponse.json({
      success: true,
      job_id: result.insertedId.toString(),
      status: 'QUEUED',
    });
  } catch (error) {
    console.error('Enqueue API Error:', error);
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : 'Internal server error',
      },
      { status: 500 }
    );
  }
}
