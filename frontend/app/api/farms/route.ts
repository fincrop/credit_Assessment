import { NextRequest, NextResponse } from 'next/server';
import { ObjectId, type Db } from 'mongodb';
import { connectToDatabase, isMongoTransientError } from '../../lib/mongodb';
import { verifyJWT } from '../../lib/jwt';
import {
  buildFarmInfoDocument,
  normalizeJourneyFarms,
} from '../../lib/farmInfoSchema';
import { ownerFields, ownerFilter } from '../../lib/ownerScope';
import { lookupDistrictName, lookupStateName } from '../../lib/india_lgd_data';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const COLLECTION = 'farmer_farms';
const FARM_INFO_COLLECTION = 'farm_info';

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

async function upsertFarmInfo(db: Db, doc: Record<string, unknown>): Promise<void> {
  const farmerId = String(doc.farmer_id || '');
  if (!farmerId) return;
  const { created_at: _c, ...rest } = doc;
  await db.collection(FARM_INFO_COLLECTION).updateOne(
    { farmer_id: farmerId },
    {
      $set: { ...rest, updated_at: doc.updated_at || new Date() },
      $setOnInsert: { created_at: doc.updated_at || new Date() },
    },
    { upsert: true }
  );
}

export async function POST(req: NextRequest) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const ownership = ownerFields(jwtPayload);

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
      farmer_benefits: farmer_benefits || {
        pm_kisan_enrolled: null,
        has_crop_insurance: null,
      },
      irrigation_type: irrigation_type || null,
      soil_type: soil_type || null,
      notes: notes || null,
      updated_at: now,
      ...ownership,
    };

    const farmIds = farmIdsFrom(farms);

    let filter: Record<string, unknown> | null = null;
    if (farmer_id && ObjectId.isValid(String(farmer_id))) {
      filter = { _id: new ObjectId(String(farmer_id)), ...ownerFilter(jwtPayload) };
    } else if (fields.agristack_farmer_id) {
      filter = { agristack_farmer_id: fields.agristack_farmer_id, ...ownerFilter(jwtPayload) };
    }

    let docId: string;
    let upserted = true;
    let updated = false;

    if (filter) {
      const existing = await col.findOne(filter);
      if (existing) {
        await col.updateOne({ _id: existing._id }, { $set: fields });
        docId = existing._id.toString();
        upserted = false;
        updated = true;
      } else if (farmer_id && ObjectId.isValid(String(farmer_id))) {
        // Explicit id belonging to someone else (or missing)
        const other = await col.findOne({ _id: new ObjectId(String(farmer_id)) });
        if (other) {
          return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
        }
        const result = await col.insertOne({
          ...fields,
          created_at: now,
        });
        docId = result.insertedId.toString();
      } else {
        const result = await col.insertOne({
          ...fields,
          created_at: now,
        });
        docId = result.insertedId.toString();
      }
    } else {
      const result = await col.insertOne({
        ...fields,
        created_at: now,
      });
      docId = result.insertedId.toString();
    }

    const loc = fields.location as {
      state?: LgdSel;
      district?: LgdSel;
      village?: LgdSel;
    };
    const pipelineFarmerId = fields.agristack_farmer_id || docId;
    const plots = normalizeJourneyFarms(fields.farms as JourneyFarm[], {
      district_lgd_code: loc?.district?.lgd_code || null,
      state_lgd_code: loc?.state?.lgd_code || null,
      village_lgd_code: loc?.village?.lgd_code || null,
    });
    const farmInfo = buildFarmInfoDocument({
      farmer_id: pipelineFarmerId,
      name: fields.farmer_name,
      mobile: fields.phone,
      farms: plots,
      farmer_benefits: fields.farmer_benefits as {
        pm_kisan_enrolled?: boolean | null;
        has_crop_insurance?: boolean | null;
      },
      state_lgd_code: loc?.state?.lgd_code || null,
      district_lgd_code: loc?.district?.lgd_code || null,
      state: loc?.state?.name || null,
      district: loc?.district?.name || null,
      village: loc?.village?.name || null,
      source: 'farmer_journey',
      created_by: ownership.created_by,
      user_id: ownership.user_id,
      now,
    });
    await upsertFarmInfo(db, farmInfo as unknown as Record<string, unknown>);

    return NextResponse.json({
      success: true,
      farmer_id: docId,
      pipeline_farmer_id: pipelineFarmerId,
      farm_ids: farmIds,
      n_plots: plots.length,
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
  if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const col = db.collection(COLLECTION);
    const farmers = await col
      .find(ownerFilter(jwtPayload))
      .sort({ created_at: -1 })
      .limit(100)
      .toArray();

    const pipelineIds = farmers.map(
      (f) => String(f.agristack_farmer_id || f._id.toString())
    );

    const infoDocs = pipelineIds.length
      ? await db
          .collection(FARM_INFO_COLLECTION)
          .find({ farmer_id: { $in: pipelineIds } })
          .toArray()
      : [];
    const infoMap = new Map(infoDocs.map((d) => [String(d.farmer_id), d]));

    /** farmer_id → latest assessment summary */
    const latestMap = new Map<
      string,
      {
        assessment_date?: string | Date;
        status?: string;
        has_assessment: boolean;
        index_score?: number | null;
        risk_category?: string | null;
      }
    >();

    if (pipelineIds.length) {
      const fromHistory = await db
        .collection('credit_assessments')
        .aggregate([
          { $match: { farmer_id: { $in: pipelineIds } } },
          { $sort: { assessment_date: -1, created_at: -1 } },
          {
            $group: {
              _id: '$farmer_id',
              assessment_date: { $first: '$assessment_date' },
              status: { $first: '$status' },
              index_score: {
                $first: {
                  $ifNull: ['$index_score', '$farmer_level.index_score'],
                },
              },
              risk_category: {
                $first: {
                  $ifNull: ['$risk_category', '$farmer_level.risk_category'],
                },
              },
            },
          },
        ])
        .toArray();

      for (const row of fromHistory) {
        latestMap.set(String(row._id), {
          assessment_date: row.assessment_date,
          status: row.status,
          has_assessment: true,
          index_score: typeof row.index_score === 'number' ? row.index_score : null,
          risk_category: row.risk_category ?? null,
        });
      }

      const missing = pipelineIds.filter((id) => !latestMap.has(id));
      if (missing.length) {
        const fromJobs = await db
          .collection('jobs')
          .aggregate([
            {
              $match: {
                farmer_id: { $in: missing },
                status: 'SUCCESS',
                result: { $exists: true, $ne: null },
              },
            },
            { $sort: { completed_at: -1, created_at: -1 } },
            {
              $group: {
                _id: '$farmer_id',
                assessment_date: { $first: '$completed_at' },
                status: { $first: '$status' },
                index_score: {
                  $first: {
                    $ifNull: [
                      '$result.farmer_level.index_score',
                      '$result.risk_assessment.index_score',
                    ],
                  },
                },
                risk_category: {
                  $first: {
                    $ifNull: [
                      '$result.farmer_level.risk_category',
                      '$result.risk_assessment.risk_category',
                    ],
                  },
                },
              },
            },
          ])
          .toArray();
        for (const row of fromJobs) {
          latestMap.set(String(row._id), {
            assessment_date: row.assessment_date,
            status: row.status,
            has_assessment: true,
            index_score: typeof row.index_score === 'number' ? row.index_score : null,
            risk_category: row.risk_category ?? null,
          });
        }
      }
    }

    return NextResponse.json({
      success: true,
      farmers: farmers.map((f) => {
        const pipeline_farmer_id = String(f.agristack_farmer_id || f._id.toString());
        const latest = latestMap.get(pipeline_farmer_id);
        const info = infoMap.get(pipeline_farmer_id);
        const loc = (f.location || {}) as {
          state?: { name?: string; lgd_code?: string } | null;
          district?: { name?: string; lgd_code?: string } | null;
          taluka?: { name?: string; lgd_code?: string } | null;
          village?: { name?: string; lgd_code?: string } | null;
        };
        const stateCode = loc.state?.lgd_code || (info?.state_lgd_code as string | undefined);
        const districtCode =
          loc.district?.lgd_code || (info?.district_lgd_code as string | undefined);
        const stateName =
          loc.state?.name ||
          (info?.state as string | undefined) ||
          lookupStateName(stateCode);
        const districtName =
          loc.district?.name ||
          (info?.district as string | undefined) ||
          lookupDistrictName(districtCode);
        const villageName =
          loc.village?.name || (info?.village as string | undefined) || null;

        const infoFarms = Array.isArray(info?.farms)
          ? (info.farms as Record<string, unknown>[])
          : [];
        const infoById = new Map(
          infoFarms.map((p) => [String(p.farm_id || ''), p])
        );
        const rawFarms = Array.isArray(f.farms) ? (f.farms as Record<string, unknown>[]) : [];
        const sourceFarms = rawFarms.length ? rawFarms : infoFarms;
        const farms = sourceFarms.map((farm, i) => {
          const id = String(farm.farm_id || farm.farm_name || `plot_${i + 1}`);
          const fromInfo = infoById.get(id);
          const area =
            typeof farm.area_ha === 'number'
              ? farm.area_ha
              : typeof fromInfo?.area_ha === 'number'
                ? fromInfo.area_ha
                : null;
          return {
            farm_id: id,
            farm_name: String(farm.farm_name || fromInfo?.farm_name || id),
            primary_crop:
              (farm.primary_crop as string | null) ||
              (fromInfo?.primary_crop as string | null) ||
              (info?.crop as string | null) ||
              null,
            area_ha: area,
          };
        });

        return {
          ...f,
          _id: f._id.toString(),
          farmer_name: f.farmer_name || info?.name || 'Unnamed farmer',
          pipeline_farmer_id,
          source: f.source || (f.agristack_farmer_id ? 'agristack_ingest' : 'farmer_journey'),
          has_assessment: !!latest?.has_assessment,
          latest_assessment_date: latest?.assessment_date ?? null,
          index_score: latest?.index_score ?? null,
          risk_category: latest?.risk_category ?? null,
          location: {
            state: stateName || stateCode ? { name: stateName || null, lgd_code: stateCode || null } : null,
            district:
              districtName || districtCode
                ? { name: districtName || null, lgd_code: districtCode || null }
                : null,
            taluka: loc.taluka || null,
            village: villageName ? { name: villageName } : loc.village || null,
          },
          farms,
        };
      }),
    });
  } catch (error) {
    const transient = isMongoTransientError(error);
    if (!transient) console.error('GET /api/farms error:', error);
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : 'Internal server error',
        transient,
      },
      { status: transient ? 503 : 500 }
    );
  }
}
