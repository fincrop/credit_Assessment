import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { ownerFilter } from '../../../lib/ownerScope';
import { assignPlotKeysClient } from '../../../lib/plotKey';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/**
 * GET /api/farm-info/[farmer_id] — owner-scoped farm_info for dashboard identity + map.
 */
export async function GET(
  req: NextRequest,
  context: { params: Promise<{ farmer_id: string }> }
) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { farmer_id: rawId } = await context.params;
  const farmerId = decodeURIComponent(rawId || '').trim();
  if (!farmerId) {
    return NextResponse.json({ error: 'farmer_id required' }, { status: 400 });
  }

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const doc = await db.collection('farm_info').findOne({
      farmer_id: farmerId,
      ...ownerFilter(jwtPayload),
    });

    if (!doc) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 });
    }

    const farms = assignPlotKeysClient(
      Array.isArray(doc.farms) ? (doc.farms as Record<string, unknown>[]) : []
    );

    return NextResponse.json({
      success: true,
      farm_info: {
        farmer_id: doc.farmer_id,
        name: doc.name || doc.farmer_name || null,
        mobile: doc.mobile || null,
        state: doc.state || null,
        district: doc.district || null,
        village: doc.village || null,
        state_lgd_code: doc.state_lgd_code || null,
        district_lgd_code: doc.district_lgd_code || null,
        latitude: doc.latitude ?? null,
        longitude: doc.longitude ?? null,
        field_area_ha: doc.field_area_ha ?? null,
        farmer_benefits: doc.farmer_benefits || null,
        source: doc.source || null,
        farms,
        updated_at: doc.updated_at || null,
      },
    });
  } catch (error) {
    console.error('GET /api/farm-info/[farmer_id] error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
