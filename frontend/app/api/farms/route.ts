import { NextRequest, NextResponse } from 'next/server';
import { ObjectId, type Db } from 'mongodb';
import { connectToDatabase } from '../../lib/mongodb';
import { verifyJWT } from '../../lib/jwt';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const COLLECTION = 'farmer_farms';
const FARM_INFO_COLLECTION = 'farm_info';

type JwtPayload = { email?: string };

type JourneyFarm = {
  farm_id?: string;
  farm_name?: string;
  boundary?: { type?: string; coordinates?: number[][][] };
  area_ha?: number;
  centroid?: { lat?: number; lng?: number };
  primary_crop?: string;
  sowing_date?: string | null;
};

type LgdSel = { lgd_code?: string; name?: string } | null;

function farmIdsFrom(farms: unknown): string[] {
  if (!Array.isArray(farms)) return [];
  return farms
    .map((f) => (f as { farm_id?: string })?.farm_id)
    .filter((id): id is string => !!id);
}

/** Build pipeline-ready farm_info doc from farmer-journey payload. */
function buildFarmInfoDoc(params: {
  pipelineFarmerId: string;
  farmerName: string;
  phone: string | null;
  location: {
    state?: LgdSel;
    district?: LgdSel;
    village?: LgdSel;
  };
  farms: JourneyFarm[];
  farmerBenefits: Record<string, unknown>;
  now: Date;
}): Record<string, unknown> {
  const { pipelineFarmerId, farmerName, phone, location, farms, farmerBenefits, now } = params;
  const primary = farms[0] || {};
  const polygons = farms
    .map((f) => f.boundary)
    .filter(
      (b): b is { type: string; coordinates: number[][][] } =>
        !!b && b.type === 'Polygon' && Array.isArray(b.coordinates)
    );

  let geometry: Record<string, unknown> | null = null;
  if (polygons.length === 1) {
    geometry = polygons[0];
  } else if (polygons.length > 1) {
    geometry = {
      type: 'MultiPolygon',
      coordinates: polygons.map((p) => p.coordinates),
    };
  }

  const fieldAreaHa = farms.reduce((sum, f) => sum + (Number(f.area_ha) || 0), 0);
  const latitude = Number(primary.centroid?.lat);
  const longitude = Number(primary.centroid?.lng);

  return {
    farmer_id: pipelineFarmerId,
    name: farmerName,
    farm_name: primary.farm_name || farmerName,
    mobile: phone,
    latitude: Number.isFinite(latitude) ? latitude : null,
    longitude: Number.isFinite(longitude) ? longitude : null,
    geometry,
    field_area_ha: fieldAreaHa > 0 ? fieldAreaHa : null,
    crop: primary.primary_crop || null,
    sowing_date: primary.sowing_date || null,
    state_lgd_code: location?.state?.lgd_code || null,
    district_lgd_code: location?.district?.lgd_code || null,
    state: location?.state?.name || null,
    district: location?.district?.name || null,
    village: location?.village?.name || null,
    farmer_benefits: {
      pm_kisan_enrolled: !!(farmerBenefits as { pm_kisan_enrolled?: boolean })?.pm_kisan_enrolled,
      has_crop_insurance: !!(farmerBenefits as { has_crop_insurance?: boolean })?.has_crop_insurance,
    },
    source: 'farmer_journey',
    status: 'active',
    updated_at: now,
  };
}

async function upsertFarmInfo(db: Db, doc: Record<string, unknown>): Promise<void> {
  const farmerId = String(doc.farmer_id || '');
  if (!farmerId) return;
  await db.collection(FARM_INFO_COLLECTION).updateOne(
    { farmer_id: farmerId },
    {
      $set: doc,
      $setOnInsert: { created_at: doc.updated_at || new Date() },
    },
    { upsert: true }
  );
}

export async function POST(req: NextRequest) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = (await verifyJWT(token)) as JwtPayload | null;
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  try {
    const body = await req.json();
    const {
      farmer_id,
      farmer_name,
      phone,
      language,
      agristack_farmer_id,
      location,
      farms,
      historical_data,
      farmer_benefits,
      irrigation_type,
      soil_type,
      notes,
    } = body;

    if (!farmer_name || !farms || farms.length === 0) {
      return NextResponse.json(
        { error: 'farmer_name and at least one farm are required' },
        { status: 400 }
      );
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection(COLLECTION);

    const now = new Date();
    const fields = {
      farmer_name: String(farmer_name).trim(),
      phone: phone ? String(phone).trim() : null,
      language: language || 'English',
      agristack_farmer_id: agristack_farmer_id ? String(agristack_farmer_id).trim() : null,
      location: location || {},
      farms: farms || [],
      historical_data: historical_data || [],
      farmer_benefits: farmer_benefits || { pm_kisan_enrolled: false, has_crop_insurance: false },
      irrigation_type: irrigation_type || null,
      soil_type: soil_type || null,
      notes: notes || null,
      updated_at: now,
    };

    const farmIds = farmIdsFrom(farms);

    // Prefer explicit farmer_id (edit), then agristack_farmer_id upsert, else insert
    let filter: Record<string, unknown> | null = null;
    if (farmer_id && ObjectId.isValid(String(farmer_id))) {
      filter = { _id: new ObjectId(String(farmer_id)) };
    } else if (fields.agristack_farmer_id) {
      filter = { agristack_farmer_id: fields.agristack_farmer_id };
    }

    let docId: string;
    let upserted = true;
    let updated = false;

    if (filter) {
      const existing = await col.findOne(filter);
      if (existing) {
        await col.updateOne(
          { _id: existing._id },
          {
            $set: {
              ...fields,
              created_by: existing.created_by || jwtPayload.email,
            },
          }
        );
        docId = existing._id.toString();
        upserted = false;
        updated = true;
      } else {
        const result = await col.insertOne({
          ...fields,
          created_by: jwtPayload.email,
          created_at: now,
        });
        docId = result.insertedId.toString();
      }
    } else {
      const result = await col.insertOne({
        ...fields,
        created_by: jwtPayload.email,
        created_at: now,
      });
      docId = result.insertedId.toString();
    }

    // Pipeline reads farm_info by farmer_id — sync journey payload there.
    // Dashboard uses agristack_farmer_id when set, else farmer_farms _id.
    const pipelineFarmerId = fields.agristack_farmer_id || docId;
    await upsertFarmInfo(
      db,
      buildFarmInfoDoc({
        pipelineFarmerId,
        farmerName: fields.farmer_name,
        phone: fields.phone,
        location: fields.location,
        farms: fields.farms as JourneyFarm[],
        farmerBenefits: fields.farmer_benefits as Record<string, unknown>,
        now,
      })
    );

    return NextResponse.json({
      success: true,
      farmer_id: docId,
      pipeline_farmer_id: pipelineFarmerId,
      farm_ids: farmIds,
      upserted,
      updated,
    });
  } catch (error) {
    console.error('POST /api/farms error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}

export async function GET(req: NextRequest) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection(COLLECTION);
    const farmers = await col.find({}).sort({ created_at: -1 }).limit(100).toArray();
    return NextResponse.json({
      success: true,
      farmers: farmers.map((f) => ({ ...f, _id: f._id.toString() })),
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
