import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/**
 * GET /api/location/villages?taluka_code=XX&q=search
 * Reads from MongoDB `lgd_villages` when populated (LGD import).
 * Requires `q` (min 2 chars) when browsing large village sets.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const talukaCode = (searchParams.get('taluka_code') || '').trim();
  const districtCode = (searchParams.get('district_code') || '').trim();
  const q = (searchParams.get('q') || '').trim();

  if (!talukaCode && !districtCode) {
    return NextResponse.json(
      { error: 'taluka_code or district_code is required' },
      { status: 400 }
    );
  }

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection('lgd_villages');

    const filter: Record<string, unknown> = {};
    if (talukaCode) filter.taluka_lgd_code = talukaCode;
    else if (districtCode) filter.district_lgd_code = districtCode;

    if (q.length >= 2) {
      filter.name = { $regex: q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $options: 'i' };
    } else if (!talukaCode) {
      return NextResponse.json({
        villages: [],
        source: 'empty',
        message: 'Provide q (min 2 chars) when searching by district only',
      });
    }

    const villages = await col
      .find(filter, {
        projection: { _id: 0, lgd_code: 1, name: 1, taluka_lgd_code: 1, district_lgd_code: 1 },
      })
      .sort({ name: 1 })
      .limit(100)
      .toArray();

    return NextResponse.json({ villages, source: villages.length > 0 ? 'mongodb' : 'empty' });
  } catch (error) {
    console.error('GET /api/location/villages error:', error);
    return NextResponse.json({ villages: [], source: 'error', error: 'Failed to load villages' });
  }
}
