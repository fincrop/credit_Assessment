import { NextRequest, NextResponse } from 'next/server';
import { jobsCollection, ownedByFilter, requireUser } from '../lib/jobs';

function iso(v: unknown): string | undefined {
  if (v instanceof Date && !Number.isNaN(v.getTime())) return v.toISOString();
  if (typeof v === 'string' && v) return v;
  return undefined;
}

export async function GET(req: NextRequest) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;
    const rows = await (await jobsCollection())
      .find(ownedByFilter(auth.user), {
        projection: {
          stage: 1,
          error: 1,
          created_at: 1,
          finished_at: 1,
          total_area_ha: 1,
          inputs: 1,
          areas: 1,
          'result.crop': 1,
          'result.season': 1,
          'result.as_of': 1,
          'result.zone_count': 1,
          'result.name': 1,
          'result.typed': 1,
        },
      })
      .sort({ created_at: -1 })
      .limit(40)
      .toArray();

    const jobs = rows.map((row) => {
      const inputs = (row.inputs || {}) as Record<string, unknown>;
      const result = (row.result || {}) as Record<string, unknown>;
      return {
        job_id: String(row._id),
        stage: row.stage ?? 'queued',
        error: row.error ?? null,
        created_at: iso(row.created_at),
        finished_at: iso(row.finished_at),
        name: String(result.name || inputs.name || 'Field'),
        crop: String(result.crop || inputs.crop || ''),
        season: String(result.season || inputs.season || ''),
        as_of: String(result.as_of || inputs.as_of || ''),
        zone_count: typeof result.zone_count === 'number' ? result.zone_count : null,
        total_area_ha: typeof row.total_area_ha === 'number' ? row.total_area_ha : null,
        areas: row.areas ?? [],
        inputs,
      };
    });
    return NextResponse.json({ jobs });
  } catch (e) {
    console.error('[monitoring/history]', e);
    return NextResponse.json({ error: 'Could not load monitoring history' }, { status: 500 });
  }
}
