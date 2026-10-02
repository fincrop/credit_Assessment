import { NextRequest, NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import { CLASSIFIABLE_CROPS } from '../../../classification/types';
import { connectToDatabase } from '../../../lib/mongodb';
import { jobsCollection, ownedBy, pipelineBaseUrl, pipelineHeaders, requireUser } from '../lib/jobs';
import { TARGET_DB, type AuthedUser } from '../../classification/lib/jobs';

const MAX_AREA_HA = 200;
const MAX_FIELDS = 5000;
const NAMED = new Set<string>(CLASSIFIABLE_CROPS);

interface IncomingArea {
  aoi_id?: string;
  name?: string;
  source?: string;
  boundary?: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  area_ha?: number;
}

export async function POST(req: NextRequest) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;

    const body = await req.json();
    const inputs = (body?.inputs || {}) as Record<string, unknown>;
    const requestedIds = Array.isArray(body?.field_ids) ? body.field_ids.map((id: unknown) => String(id)) : null;
    const classJobId = String(body?.classification_job_id || inputs.classification_job_id || '');

    let areas = (body?.areas || []) as IncomingArea[];
    let totalHa = 0;
    if (classJobId && requestedIds && requestedIds.length > 0) {
      if (requestedIds.length > MAX_FIELDS) {
        return NextResponse.json(
          { error: `Select up to ${MAX_FIELDS} fields in one monitoring run.` },
          { status: 400 },
        );
      }
      const loaded = await loadClassifiedFields(classJobId, requestedIds, auth.user);
      if ('error' in loaded) {
        return NextResponse.json({ error: loaded.error }, { status: loaded.status });
      }
      inputs.classification_job_id = classJobId;
      inputs.field_ids = loaded.ids;
      inputs.farm_count = loaded.ids.length;
      inputs.crops = loaded.crops;
      inputs.crop = loaded.crops.length === 1 ? loaded.crops[0] : 'multiple';
      inputs.name = String(inputs.name || loaded.name || inputs.crop);
      areas = [];
      totalHa = loaded.areaHa;
    } else {
      if (!Array.isArray(areas) || areas.length === 0) {
        return NextResponse.json({ error: 'Draw or upload one field boundary.' }, { status: 400 });
      }
      const area = areas[0];
      const geom = area?.boundary;
      if (!geom || (geom.type !== 'Polygon' && geom.type !== 'MultiPolygon')) {
        return NextResponse.json({ error: 'The field needs a polygon boundary.' }, { status: 400 });
      }
      const ha = Number(area.area_ha) || 0;
      if (ha > MAX_AREA_HA) {
        return NextResponse.json(
          { error: `This field is ${ha.toFixed(0)} ha. Monitoring runs one field up to ${MAX_AREA_HA} ha.` },
          { status: 400 },
        );
      }
      const crop = String(inputs.crop || '').trim();
      if (!crop) {
        return NextResponse.json({ error: 'Choose the crop on this field.' }, { status: 400 });
      }
      inputs.name = String(inputs.name || area.name || crop).trim();
      inputs.crop = crop;
      areas = [area];
      totalHa = Number(area.area_ha) || 0;
    }

    const now = new Date();
    const collection = await jobsCollection();
    const { insertedId } = await collection.insertOne({
      kind: 'crop_monitoring',
      stage: 'queued',
      percent: null,
      message: 'Queued',
      areas,
      inputs,
      total_area_ha: totalHa,
      requested_by: auth.user.email,
      requested_by_user_id: auth.user.id,
      created_at: now,
      updated_at: now,
    });
    const jobId = insertedId.toString();

    const base = pipelineBaseUrl();
    if (!base) {
      await collection.updateOne(
        { _id: insertedId },
        {
          $set: {
            stage: 'failed',
            error: 'No monitoring service configured. Set PIPELINE_API_URL to the FastAPI service.',
            updated_at: new Date(),
          },
        }
      );
      return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
    }

    try {
      const res = await fetch(`${base}/v1/jobs/monitor`, {
        method: 'POST',
        headers: pipelineHeaders(),
        body: JSON.stringify({ job_id: jobId, areas, inputs }),
        signal: AbortSignal.timeout(Number(process.env.PIPELINE_ENQUEUE_TIMEOUT_MS || 20000)),
      });
      if (!res.ok) {
        const detail = (await res.text()).slice(0, 200);
        await collection.updateOne(
          { _id: insertedId },
          { $set: { stage: 'failed', error: `Monitoring service returned HTTP ${res.status}. ${detail}`, updated_at: new Date() } }
        );
        return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
      }
    } catch (e) {
      await collection.updateOne(
        { _id: insertedId },
        {
          $set: {
            stage: 'failed',
            error: `Could not reach the monitoring service. ${e instanceof Error ? e.message : ''}`.trim(),
            updated_at: new Date(),
          },
        }
      );
      return NextResponse.json({ job_id: jobId, stage: 'failed' }, { status: 202 });
    }

    return NextResponse.json({ job_id: jobId, stage: 'queued' }, { status: 202 });
  } catch (e) {
    console.error('[monitoring/enqueue]', e);
    return NextResponse.json({ error: 'Could not queue monitoring' }, { status: 500 });
  }
}

async function loadClassifiedFields(
  jobId: string,
  fieldIds: string[],
  user: AuthedUser,
): Promise<{ ids: string[]; crops: string[]; name: string; areaHa: number } | { error: string; status: number }> {
  if (!ObjectId.isValid(jobId)) {
    return { error: 'That classification id is not valid.', status: 400 };
  }
  const { client } = await connectToDatabase();
  const doc = await client.db(TARGET_DB).collection('classification_jobs').findOne({ _id: new ObjectId(jobId) });
  if (!doc || !ownedBy(doc, user)) {
    return { error: 'Classification not found.', status: 404 };
  }
  if (doc.stage !== 'complete') {
    return { error: 'That classification is not finished.', status: 409 };
  }
  const features = (doc.result as { fields?: { features?: GeoJSON.Feature[] } } | undefined)?.fields?.features || [];
  const wanted = new Set(fieldIds);
  const ids: string[] = [];
  const crops: string[] = [];
  let areaHa = 0;
  for (const feature of features) {
    const props = (feature.properties || {}) as { field_id?: string; crop?: string; area_ha?: number };
    const id = String(props.field_id || '');
    const crop = String(props.crop || '');
    if (!wanted.has(id) || !NAMED.has(crop)) continue;
    ids.push(id);
    if (!crops.includes(crop)) crops.push(crop);
    areaHa += Number(props.area_ha) || 0;
  }
  if (ids.length === 0) {
    return { error: 'None of those fields are named crops on that classification.', status: 400 };
  }
  const inputs = (doc.inputs || {}) as { region_name?: string };
  const result = (doc.result || {}) as { aoi_name?: string };
  return {
    ids,
    crops,
    name: result.aoi_name || inputs.region_name || 'Classification',
    areaHa,
  };
}
