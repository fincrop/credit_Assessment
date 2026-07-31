import { NextRequest, NextResponse } from 'next/server';
import { getTalukas } from '../../../lib/india_lgd_data';
import { connectToDatabase } from '../../../lib/mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/**
 * GET /api/location/talukas?district_code=XX&q=optional
 * Prefers MongoDB `lgd_talukas` when seeded; falls back to bundled LGD JSON.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const districtCode = (searchParams.get('district_code') || '').trim();
  const q = (searchParams.get('q') || '').trim().toLowerCase();

  if (!districtCode) {
    return NextResponse.json({ error: 'district_code is required' }, { status: 400 });
  }

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection('lgd_talukas');
    const filter: Record<string, unknown> = { district_lgd_code: districtCode };
    if (q) {
      filter.name = { $regex: q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $options: 'i' };
    }
    const mongoTalukas = await col
      .find(filter, { projection: { _id: 0, lgd_code: 1, name: 1, district_lgd_code: 1, state_lgd_code: 1 } })
      .sort({ name: 1 })
      .limit(500)
      .toArray();

    if (mongoTalukas.length > 0) {
      return NextResponse.json({ talukas: mongoTalukas, source: 'mongodb' });
    }
  } catch {
    /* fall through to static */
  }

  let talukas = getTalukas(districtCode);
  if (q) {
    talukas = talukas.filter((t) => t.name.toLowerCase().includes(q) || t.lgd_code.includes(q));
  }
  return NextResponse.json({
    talukas,
    source: talukas.length > 0 ? 'static' : 'empty',
  });
}
