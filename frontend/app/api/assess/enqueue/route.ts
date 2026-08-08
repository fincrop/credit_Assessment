import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { ownerFilter, ownerFields } from '../../../lib/ownerScope';

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

/** Preserve tri-state: true | false | null. Do not coerce with !!. */
function asTriState(v: unknown): boolean | null {
  if (v === true || v === false) return v;
  return null;
}

async function assertFarmerOwned(
  farmerId: string,
  user: { id: string; email: string }
): Promise<boolean> {
  const { client } = await connectToDatabase();
  const db = client.db(TARGET_DB);
  const ownership = ownerFilter(user);

  const farmInfo = await db.collection('farm_info').findOne(
    { farmer_id: farmerId, ...ownership },
    { projection: { _id: 1 } }
  );
  if (farmInfo) return true;

  const journey = await db.collection('farmer_farms').findOne(
    {
      $and: [
        ownership,
        {
          $or: [
            { agristack_farmer_id: farmerId },
            // journey docs use ObjectId string as pipeline id when no agristack id
          ],
        },
      ],
    },
    { projection: { _id: 1 } }
  );
  if (journey) return true;

  // Also allow journey _id as farmer_id
  try {
    const { ObjectId } = await import('mongodb');
    if (ObjectId.isValid(farmerId)) {
      const byId = await db.collection('farmer_farms').findOne(
        { _id: new ObjectId(farmerId), ...ownership },
        { projection: { _id: 1 } }
      );
      if (byId) return true;
    }
  } catch {
    /* ignore */
  }

  return false;
}

export async function POST(req: NextRequest) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const jwtPayload = await verifyJWT(token);
    if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

    const body = await req.json();
    const { farmer_id } = body;
    const pm_kisan_enrolled = asTriState(body.pm_kisan_enrolled);
    const has_crop_insurance = asTriState(body.has_crop_insurance);

    if (farmer_id == null || typeof farmer_id !== 'string' || !farmer_id.trim()) {
      return NextResponse.json(
        { error: 'farmer_id must be a non-empty string' },
        { status: 400 }
      );
    }

    const farmerId = String(farmer_id).trim();
    const owned = await assertFarmerOwned(farmerId, jwtPayload);
    if (!owned) {
      return NextResponse.json(
        { error: 'Farmer not found or not owned by your account' },
        { status: 403 }
      );
    }

    const ownership = ownerFields(jwtPayload);
    const base = pipelineBaseUrl();
    if (base) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const key = pipelineApiKey();
      if (key) headers['X-API-Key'] = key;

      const assessUrl = `${base}/v1/jobs/assess`;

      // Fail fast if PIPELINE_API_URL points at the wrong process (HTML apps, Next, etc.)
      try {
        const healthRes = await fetch(`${base}/health`, {
          method: 'GET',
          headers: key ? { 'X-API-Key': key } : undefined,
          signal: AbortSignal.timeout(5000),
        });
        const healthText = await healthRes.text();
        const looksHtml =
          /^\s*<!doctype/i.test(healthText) || /^\s*<html/i.test(healthText);
        let healthJson: Record<string, unknown> | null = null;
        try {
          healthJson = healthText ? JSON.parse(healthText) : null;
        } catch {
          healthJson = null;
        }
        const looksLikeOurApi =
          healthJson != null &&
          (healthJson.status === 'ok' || healthJson.pipeline_loaded != null);
        if (!healthRes.ok || looksHtml || !looksLikeOurApi) {
          console.error('[assess/enqueue] pipeline health failed', {
            base,
            status: healthRes.status,
            preview: healthText.slice(0, 160),
          });
          return NextResponse.json(
            {
              error:
                `PIPELINE_API_URL (${base}) is not the Agri-Credit FastAPI service. ` +
                `Expected GET /health JSON with status/pipeline_loaded. ` +
                `Got HTTP ${healthRes.status}` +
                (looksHtml ? ' (HTML — wrong process on this port?).' : '.') +
                ` Start: cd backend/Credit_assessment && uvicorn api.app:app --host 0.0.0.0 --port 8000`,
            },
            { status: 502 }
          );
        }
      } catch (e) {
        console.error('[assess/enqueue] pipeline health unreachable', base, e);
        return NextResponse.json(
          {
            error:
              `Cannot reach pipeline at ${base}/health. ` +
              `Is uvicorn running? (${e instanceof Error ? e.message : 'network error'})`,
          },
          { status: 502 }
        );
      }

      console.info('[assess/enqueue] POST', assessUrl);
      const res = await fetch(assessUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          farmer_id: farmerId,
          pm_kisan_enrolled,
          has_crop_insurance,
        }),
      });

      const text = await res.text();
      let data: unknown;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        const looksHtml =
          /^\s*<!doctype/i.test(text) || /^\s*<html/i.test(text);
        console.error('[assess/enqueue] non-JSON upstream', {
          assessUrl,
          status: res.status,
          preview: text.slice(0, 200),
        });
        return NextResponse.json(
          {
            error: looksHtml
              ? `PIPELINE_API_URL (${base}) returned HTML ${res.status} for /v1/jobs/assess — not FastAPI. Check port conflict or Render API URL.`
              : `Pipeline API invalid JSON (${res.status}) from ${assessUrl}: ${text.slice(0, 200)}`,
          },
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

      // Stamp ownership on the job if pipeline returned a job_id we can update
      const jobId = (data as { job_id?: string })?.job_id;
      if (jobId) {
        try {
          const { ObjectId } = await import('mongodb');
          if (ObjectId.isValid(jobId)) {
            const { client } = await connectToDatabase();
            const db = client.db(TARGET_DB);
            await db.collection('jobs').updateOne(
              { _id: new ObjectId(jobId) },
              {
                $set: {
                  requested_by: ownership.created_by,
                  requested_by_user_id: ownership.user_id,
                },
              }
            );
          }
        } catch {
          /* non-fatal */
        }
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
      farmer_id: farmerId,
      pm_kisan_enrolled,
      has_crop_insurance,
      status: 'QUEUED',
      created_at: new Date(),
      updated_at: new Date(),
      result: null,
      error: null,
      requested_by: ownership.created_by,
      requested_by_user_id: ownership.user_id,
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
