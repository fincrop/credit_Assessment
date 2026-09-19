import { NextRequest, NextResponse } from 'next/server';
import { jobsCollection, ownedByFilter, requireUser } from '../lib/jobs';

const LIMIT = 50;

/**
 * The classified field layer is the bulk of a completed job. The list only
 * needs names, dates and the summary stats — reopen fetches the polygons
 * from `/result/:id`.
 */
const LIST_PROJECTION = {
  stage: 1,
  error: 1,
  created_at: 1,
  updated_at: 1,
  finished_at: 1,
  total_area_ha: 1,
  inputs: 1,
  areas: 1,
  'result.aoi_name': 1,
  'result.season': 1,
  'result.year': 1,
  'result.field_count': 1,
  'result.classified_area_ha': 1,
  'result.total_area_ha': 1,
  'result.mean_confidence': 1,
  'result.stats': 1,
} as const;

function iso(v: unknown): string | undefined {
  if (v instanceof Date && !Number.isNaN(v.getTime())) return v.toISOString();
  if (typeof v === 'string' && v) return v;
  return undefined;
}

export async function GET(req: NextRequest) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;

    const collection = await jobsCollection();
    const docs = await collection
      .find(ownedByFilter(auth.user), { projection: LIST_PROJECTION })
      .sort({ created_at: -1 })
      .limit(LIMIT)
      .toArray();

    const jobs = docs.map((doc) => {
      const result = (doc.result || {}) as Record<string, unknown>;
      const inputs = (doc.inputs || {}) as Record<string, unknown>;
      const areas = Array.isArray(doc.areas) ? doc.areas : [];
      const named =
        (typeof inputs.region_name === 'string' && inputs.region_name.trim()) ||
        (typeof result.aoi_name === 'string' && result.aoi_name.trim()) ||
        (typeof areas[0]?.name === 'string' && areas[0].name) ||
        'Area of interest';
      const total =
        Number(result.total_area_ha) ||
        Number(doc.total_area_ha) ||
        areas.reduce((s: number, a: { area_ha?: number }) => s + (Number(a?.area_ha) || 0), 0);

      return {
        job_id: String(doc._id),
        stage: doc.stage || 'queued',
        created_at: iso(doc.created_at),
        finished_at: iso(doc.finished_at) || iso(doc.updated_at),
        error: typeof doc.error === 'string' ? doc.error : undefined,
        aoi_name: named,
        season: result.season || inputs.season || 'kharif',
        year: Number(result.year) || Number(inputs.year) || new Date().getFullYear(),
        total_area_ha: total,
        field_count: typeof result.field_count === 'number' ? result.field_count : undefined,
        classified_area_ha:
          typeof result.classified_area_ha === 'number' ? result.classified_area_ha : undefined,
        mean_confidence:
          typeof result.mean_confidence === 'number' ? result.mean_confidence : undefined,
        stats: Array.isArray(result.stats) ? result.stats : [],
        areas,
        inputs,
      };
    });

    return NextResponse.json({ jobs });
  } catch (e) {
    console.error('[classification/history]', e);
    return NextResponse.json({ error: 'Could not load classification history' }, { status: 500 });
  }
}
