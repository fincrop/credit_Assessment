import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';
import { verifyJWT } from '../../lib/jwt';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/**
 * GET /api/farm-info?q=... — search AgriStack-sourced farmers in farm_info
 * for linking an existing farmer in the Farmer Journey.
 */
export async function GET(req: NextRequest) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const q = (req.nextUrl.searchParams.get('q') || '').trim();

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection('farm_info');

    const filter = q
      ? {
          $or: [
            { farmer_id: { $regex: q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $options: 'i' } },
            { farmer_name: { $regex: q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $options: 'i' } },
            { name: { $regex: q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), $options: 'i' } },
          ],
        }
      : {};

    const farmers = await col
      .find(filter, {
        projection: {
          farmer_id: 1,
          farmer_name: 1,
          name: 1,
          state: 1,
          district: 1,
          latitude: 1,
          longitude: 1,
          field_area_ha: 1,
        },
      })
      .sort({ updated_at: -1 })
      .limit(30)
      .toArray();

    return NextResponse.json({
      success: true,
      farmers: farmers.map((f) => ({
        farmer_id: f.farmer_id,
        farmer_name: f.farmer_name || f.name || 'Unknown',
        state: f.state ?? null,
        district: f.district ?? null,
        latitude: f.latitude ?? null,
        longitude: f.longitude ?? null,
        field_area_ha: f.field_area_ha ?? null,
      })),
    });
  } catch (error) {
    console.error('GET /api/farm-info error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
