/**
 * Shared AgriStack → farm_info ingest helpers (used by /api/ingest-farmer
 * and /api/agristack/prepare-farmer).
 */

import type { Db } from 'mongodb';
import {
  buildFarmInfoDocument,
  normalizeAgriStackLands,
} from './farmInfoSchema';
import { ownerFilter } from './ownerScope';

const COLLECTION = 'farm_info';
const FARMER_FARMS = 'farmer_farms';

export type Ownership = { created_by: string; user_id: string };

function normalizeArray(v: unknown): unknown[] {
  if (!v) return [];
  return Array.isArray(v) ? v : [v];
}

export function parseAgriStackPayloads(raw: unknown): unknown[] {
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return raw
      .map((x) => {
        const item = x as Record<string, unknown>;
        const response = item?.response as Record<string, unknown> | undefined;
        return response?.data ?? item?.response ?? x;
      })
      .filter(Boolean);
  }
  const obj = raw as Record<string, unknown>;
  return [obj?.data ?? raw];
}

export function extractCorrelationIds(payloads: unknown[]): string[] {
  const out = new Set<string>();
  for (const p of payloads) {
    const o = p as Record<string, unknown>;
    const msg = o?.message as Record<string, unknown> | undefined;
    const data = o?.data as Record<string, unknown> | undefined;
    const dataMsg = data?.message as Record<string, unknown> | undefined;
    const id =
      (typeof msg?.correlation_id === 'string' && msg.correlation_id) ||
      (typeof dataMsg?.correlation_id === 'string' && dataMsg.correlation_id) ||
      (typeof o?.correlation_id === 'string' && o.correlation_id) ||
      null;
    if (id) out.add(id);
  }
  return Array.from(out);
}

export function extractFarmersFromPayload(
  payload: unknown,
  ownership: Ownership
): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  const o = payload as Record<string, unknown> | null;
  if (!o) return out;
  const msg = (o.message || (o.data as Record<string, unknown>)?.message) as
    | Record<string, unknown>
    | undefined;
  const searchResponses = normalizeArray(msg?.search_response);

  for (const searchResRaw of searchResponses) {
    const searchRes = searchResRaw as Record<string, unknown>;
    const dataBlock = searchRes?.data as Record<string, unknown> | undefined;
    const regRecordsRaw = dataBlock?.reg_records;
    const regRecordsAsList = Array.isArray(regRecordsRaw)
      ? regRecordsRaw
      : regRecordsRaw && typeof regRecordsRaw === 'object'
        ? [regRecordsRaw]
        : [];

    const registryRecords = [
      ...normalizeArray(searchRes?.farmer_registry),
      ...regRecordsAsList,
      ...normalizeArray(dataBlock?.farmer_registry),
    ];

    for (const regRaw of registryRecords) {
      const reg = regRaw as Record<string, unknown>;
      if (reg?.FarmerData && typeof reg.FarmerData === 'object') {
        const fd = reg.FarmerData as Record<string, unknown>;
        const farmerId = fd.farmer_id;
        if (!farmerId) continue;
        const name = String(fd.farmer_name || fd.name || 'Unknown');
        const lands = normalizeArray(reg.land_data).filter(Boolean) as Record<
          string,
          unknown
        >[];
        const { farms, parcel_ingest_stats } = normalizeAgriStackLands(lands, {
          ...fd,
          state_lgd_code: fd.state_lgd_code ?? reg.state_lgd_code,
        });
        const doc = buildFarmInfoDocument({
          farmer_id: String(farmerId),
          name,
          mobile: fd.mobile_no != null ? String(fd.mobile_no) : null,
          farms,
          parcel_ingest_stats,
          farmer_profile: {
            gender: fd.gender,
            dob: fd.dob,
            address: fd.address,
            farmer_category: fd.farmer_category,
            caste_category: fd.caste_category,
            aadhaar_type: fd.aadhaar_type,
            village_lgd_code: fd.village_lgd_code,
            sub_district_lgd_code: fd.sub_district_lgd_code,
          },
          state_lgd_code:
            (fd.state_lgd_code as string) ||
            (reg.state_lgd_code as string) ||
            null,
          district_lgd_code: (fd.district_lgd_code as string) || null,
          farmer_benefits: {
            pm_kisan_enrolled: null,
            has_crop_insurance: null,
          },
          source: 'agristack_ingest',
          created_by: ownership.created_by,
          user_id: ownership.user_id,
        });
        out.push({
          ...doc,
          created_at: new Date(),
        });
        continue;
      }

      const farmerIdObj = (reg?.farmer_identifier || {}) as Record<string, unknown>;
      const farmerId =
        farmerIdObj?.farmer_id ||
        farmerIdObj?.id ||
        reg?.farmer_id ||
        reg?.id ||
        reg?.farmerId;
      if (!farmerId) continue;

      const personal = (reg?.farmer_personal_details ||
        reg?.personal_details ||
        {}) as Record<string, unknown>;
      const name = String(
        personal?.farmer_name || personal?.name || reg?.name || 'Unknown'
      );

      const farms = normalizeArray(
        reg?.farmer_farm_details || reg?.farm_details || reg?.land_parcels
      );
      const primaryFarm = (farms[0] || {}) as Record<string, unknown>;
      const geom =
        primaryFarm?.farm_geometry ||
        primaryFarm?.geometry ||
        primaryFarm?.parcel_geometry ||
        null;

      out.push({
        farmer_id: String(farmerId),
        name,
        geometry: geom,
        field_area_ha:
          primaryFarm?.area_unit_ha ||
          (primaryFarm?.plot_area
            ? Number(primaryFarm.plot_area) / 10000
            : null),
        crop: null,
        sowing_date: null,
        state_lgd_code: primaryFarm?.state_lgd_code || null,
        farmer_benefits: {
          pm_kisan_enrolled: !!(
            reg?.pm_kisan_enrolled ||
            (reg?.farmer_benefits as Record<string, unknown>)?.pm_kisan_enrolled
          ),
          has_crop_insurance: !!(
            reg?.crop_insurance_details ||
            (reg?.farmer_benefits as Record<string, unknown>)?.has_crop_insurance
          ),
        },
        source: 'agristack_ingest',
        status: 'active',
        created_by: ownership.created_by,
        user_id: ownership.user_id,
        created_at: new Date(),
        updated_at: new Date(),
      });
    }
  }

  return out;
}

export async function findWebhookBodiesByCorrelation(
  db: Db,
  correlationIds: string[]
): Promise<unknown[]> {
  if (!correlationIds.length) return [];
  const collections = [
    db.collection('webhook_farmers_responses'),
    db.collection('webhook_responses'),
    db.collection('webhook_kdss_responses'),
  ];
  const escaped = correlationIds.map((id) =>
    id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  );
  const bodies: unknown[] = [];
  for (const col of collections) {
    const docs = await col
      .find({
        $or: [
          { 'body.message.correlation_id': { $in: correlationIds } },
          { 'body.data.message.correlation_id': { $in: correlationIds } },
          { 'body.message.ack.correlation_id': { $in: correlationIds } },
          { 'body.data.message.ack.correlation_id': { $in: correlationIds } },
          { 'body.header.correlation_id': { $in: correlationIds } },
          { 'body.correlation_id': { $in: correlationIds } },
          ...escaped.map((id) => ({ rawBody: { $regex: id } })),
        ],
      })
      .sort({ receivedAt: -1 })
      .limit(10)
      .toArray();
    for (const doc of docs) {
      bodies.push(doc.body, (doc.body as Record<string, unknown>)?.data);
    }
  }
  return bodies.filter(Boolean);
}

export async function upsertFarmersFromDocs(
  db: Db,
  farmersToInsert: Record<string, unknown>[],
  ownership: Ownership,
  jwtUser: { id: string; email: string }
): Promise<{
  farmer_ids: string[];
  created: string[];
  updated: string[];
  errors: { id: unknown; error: string }[];
  plot_counts: { farmer_id: unknown; n_plots: number; n_included: number }[];
}> {
  const collection = db.collection(COLLECTION);
  const farmerFarms = db.collection(FARMER_FARMS);
  const insertedIds: string[] = [];
  const created: string[] = [];
  const updated: string[] = [];
  const errors: { id: unknown; error: string }[] = [];

  for (const doc of farmersToInsert) {
    try {
      const farmerId = String(doc.farmer_id || '').trim();
      if (!farmerId) {
        errors.push({ id: '(missing)', error: 'farmer_id missing on extracted record' });
        continue;
      }

      const {
        created_at: docCreatedAt,
        _id: _ignoredId,
        ...rest
      } = doc as Record<string, unknown> & { created_at?: Date; _id?: unknown };

      const ownedDoc = {
        ...rest,
        farmer_id: farmerId,
        created_by: ownership.created_by,
        user_id: ownership.user_id,
        updated_at: new Date(),
      };

      const result = await collection.updateOne(
        { farmer_id: farmerId },
        {
          $set: ownedDoc,
          $setOnInsert: { created_at: docCreatedAt || new Date() },
        },
        { upsert: true }
      );
      insertedIds.push(farmerId);
      if (result.upsertedCount > 0) created.push(farmerId);
      else updated.push(farmerId);

      const farmsArr = Array.isArray(doc.farms) ? doc.farms : [];
      const mirrorFarms = farmsArr.map((f: Record<string, unknown>, i: number) => ({
        farm_id: String(f.farm_id || `plot_${i + 1}`),
        farm_name: f.farm_name || f.farm_id || `Plot ${i + 1}`,
        boundary: f.geometry || null,
        area_ha: f.area_ha ?? null,
        centroid: f.centroid || null,
        primary_crop: f.primary_crop || doc.crop || null,
        sowing_date: f.sowing_date || doc.sowing_date || null,
      }));
      const mirrorFilter = {
        agristack_farmer_id: farmerId,
        ...ownerFilter(jwtUser),
      };
      const existingMirror = await farmerFarms.findOne(mirrorFilter);
      const now = new Date();
      const mirrorFields = {
        farmer_name: doc.name || 'Unknown',
        phone: doc.mobile || null,
        language: 'English',
        agristack_farmer_id: farmerId,
        location: {
          state: doc.state ? { name: doc.state, lgd_code: doc.state_lgd_code } : null,
          district: doc.district
            ? { name: doc.district, lgd_code: doc.district_lgd_code }
            : null,
          village: doc.village ? { name: doc.village } : null,
        },
        farms: mirrorFarms,
        farmer_benefits: doc.farmer_benefits || {
          pm_kisan_enrolled: null,
          has_crop_insurance: null,
        },
        source: 'agristack_ingest',
        updated_at: now,
        ...ownership,
      };
      if (existingMirror) {
        await farmerFarms.updateOne({ _id: existingMirror._id }, { $set: mirrorFields });
      } else {
        await farmerFarms.insertOne({
          ...mirrorFields,
          created_at: now,
        });
      }
    } catch (err) {
      console.error('ingest upsert failed:', err);
      errors.push({
        id: doc.farmer_id,
        error: err instanceof Error ? err.message : 'Unknown',
      });
    }
  }

  const plot_counts = farmersToInsert.map((d) => ({
    farmer_id: d.farmer_id,
    n_plots: Array.isArray(d.farms) ? d.farms.length : 0,
    n_included: Array.isArray(d.farms)
      ? d.farms.filter(
          (f: { included_in_assessment?: boolean }) => f.included_in_assessment !== false
        ).length
      : 0,
  }));

  return { farmer_ids: insertedIds, created, updated, errors, plot_counts };
}

/** Resolve farmers from a Seek ACK or full webhook body, including correlation lookup. */
export async function resolveFarmersForIngest(
  db: Db,
  agristackResponse: unknown,
  ownership: Ownership
): Promise<{
  farmers: Record<string, unknown>[];
  correlationIds: string[];
}> {
  const payloads = parseAgriStackPayloads(agristackResponse);
  let farmers = payloads.flatMap((p) => extractFarmersFromPayload(p, ownership));
  const correlationIds = extractCorrelationIds(payloads);

  if (farmers.length === 0 && correlationIds.length > 0) {
    const bodies = await findWebhookBodiesByCorrelation(db, correlationIds);
    for (const body of bodies) {
      farmers = farmers.concat(extractFarmersFromPayload(body, ownership));
    }
  }

  return { farmers, correlationIds };
}
