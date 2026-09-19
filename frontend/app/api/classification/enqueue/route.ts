import { NextRequest, NextResponse } from 'next/server';
import { jobsCollection, pipelineBaseUrl, pipelineHeaders, requireUser } from '../lib/jobs';

/** Hard ceiling on a single job. Above this the extraction cost stops being
 *  predictable and the user should tile the area instead. */
const MAX_AREA_HA = 50_000;
const MAX_AREAS = 25;

interface IncomingArea {
  aoi_id?: string;
  name?: string;
  source?: string;
  boundary?: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  area_ha?: number;
}

function validate(body: unknown): { error: string } | { areas: IncomingArea[]; inputs: Record<string, unknown> } {
  if (!body || typeof body !== 'object') return { error: 'Body must be an object' };
  const { areas, inputs } = body as { areas?: unknown; inputs?: unknown };

  if (!Array.isArray(areas) || areas.length === 0) {
    return { error: 'At least one area of interest is required' };
  }
  if (areas.length > MAX_AREAS) {
    return { error: `At most ${MAX_AREAS} areas per job` };
  }

  let total = 0;
  for (const a of areas as IncomingArea[]) {
    const geom = a?.boundary;
    if (!geom || (geom.type !== 'Polygon' && geom.type !== 'MultiPolygon')) {
      return { error: 'Every area needs a Polygon or MultiPolygon boundary' };
    }
    if (!Array.isArray(geom.coordinates) || geom.coordinates.length === 0) {
      return { error: `Area "${a?.name ?? 'unnamed'}" has an empty geometry` };
    }
    total += Number(a?.area_ha) || 0;
  }
  if (total > MAX_AREA_HA) {
    return {
      error: `Total area ${Math.round(total).toLocaleString()} ha exceeds the ${MAX_AREA_HA.toLocaleString()} ha limit for one job. Split it into smaller areas.`,
    };
  }
  if (!inputs || typeof inputs !== 'object') {
    return { error: 'Model inputs are required' };
  }
  return { areas: areas as IncomingArea[], inputs: inputs as Record<string, unknown> };
}

export async function POST(req: NextRequest) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;

    const parsed = validate(await req.json());
    if ('error' in parsed) {
      return NextResponse.json({ error: parsed.error }, { status: 400 });
    }
    const { areas, inputs } = parsed;
    const region = typeof inputs.region_name === 'string' ? inputs.region_name.trim() : '';
    if (region) {
      inputs.region_name = region;
      for (const a of areas) a.name = region;
    }

    const now = new Date();
    const doc = {
      kind: 'crop_classification',
      stage: 'queued' as const,
      percent: null as number | null,
      checks: [] as unknown[],
      areas,
      inputs,
      total_area_ha: areas.reduce((s, a) => s + (Number(a.area_ha) || 0), 0),
      requested_by: auth.user.email,
      requested_by_user_id: auth.user.id,
      created_at: now,
      updated_at: now,
    };

    const collection = await jobsCollection();
    const { insertedId } = await collection.insertOne(doc);
    const jobId = insertedId.toString();

    // Hand off to the pipeline service when one is configured. The job row is
    // written first either way, so a backend that is down leaves a visible
    // queued job rather than a silent no-op the user has to guess about.
    const base = pipelineBaseUrl();
    if (!base) {
      await collection.updateOne(
        { _id: insertedId },
        {
          $set: {
            stage: 'failed',
            error:
              'No classification service configured. Set PIPELINE_API_URL to the FastAPI service exposing POST /v1/jobs/classify.',
            updated_at: new Date(),
          },
        }
      );
      return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
    }

    try {
      const res = await fetch(`${base}/v1/jobs/classify`, {
        method: 'POST',
        headers: pipelineHeaders(),
        body: JSON.stringify({ job_id: jobId, areas, inputs }),
        signal: AbortSignal.timeout(Number(process.env.PIPELINE_ENQUEUE_TIMEOUT_MS || 20000)),
      });
      if (!res.ok) {
        const detail = (await res.text()).slice(0, 200);
        await collection.updateOne(
          { _id: insertedId },
          {
            $set: {
              stage: 'failed',
              error: `Classification service returned HTTP ${res.status}. ${detail}`,
              updated_at: new Date(),
            },
          }
        );
        return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
      }
    } catch (e) {
      await collection.updateOne(
        { _id: insertedId },
        {
          $set: {
            stage: 'failed',
            error: `Could not reach the classification service at ${base}. ${
              e instanceof Error ? e.message : ''
            }`.trim(),
            updated_at: new Date(),
          },
        }
      );
      return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
    }

    return NextResponse.json({ job_id: jobId, stage: 'queued' }, { status: 202 });
  } catch (e) {
    console.error('[classification/enqueue]', e);
    return NextResponse.json({ error: 'Could not queue the classification' }, { status: 500 });
  }
}
