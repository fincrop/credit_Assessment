import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';
import { verifyJWT } from '../../lib/jwt';
import {
  buildFarmInfoDocument,
  normalizeAgriStackLands,
} from '../../lib/farmInfoSchema';
import { ownerFields, ownerFilter } from '../../lib/ownerScope';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const COLLECTION = 'farm_info';
const FARMER_FARMS = 'farmer_farms';

export async function POST(req: NextRequest) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const jwtPayload = await verifyJWT(token);
    if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const ownership = ownerFields(jwtPayload);

    const { agristack_response } = await req.json();

    if (!agristack_response) {
      return NextResponse.json({ error: 'Missing agristack_response in body' }, { status: 400 });
    }

    const normalizeArray = (v: any): any[] => {
      if (!v) return [];
      return Array.isArray(v) ? v : [v];
    };

    const parsePayloads = (raw: any): any[] => {
      if (!raw) return [];
      if (Array.isArray(raw)) {
        return raw.map((x) => x?.response?.data ?? x?.response ?? x).filter(Boolean);
      }
      return [raw?.data ?? raw];
    };

    const extractCorrelationIds = (payloads: any[]): string[] => {
      const out = new Set<string>();
      for (const p of payloads) {
        const id = p?.message?.correlation_id || p?.data?.message?.correlation_id || p?.correlation_id;
        if (id && typeof id === 'string') out.add(id);
      }
      return Array.from(out);
    };

    const extractFarmersFromPayload = (payload: any): any[] => {
      const out: any[] = [];
      const searchResponses = normalizeArray(payload?.message?.search_response || payload?.data?.message?.search_response);

      for (const searchRes of searchResponses) {
        const dataBlock = searchRes?.data;
        // agristack_croparea_v3_get_data: reg_records is one object { FarmerData, land_data[], ... } not an array of rows
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

        for (const reg of registryRecords) {
          // Nested FarmerData + land_data (Seek / webhook full payload)
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

          const farmerIdObj = reg?.farmer_identifier || {};
          const farmerId =
            farmerIdObj?.farmer_id ||
            farmerIdObj?.id ||
            reg?.farmer_id ||
            reg?.id ||
            reg?.farmerId;
          if (!farmerId) continue;

          const personal = reg?.farmer_personal_details || reg?.personal_details || {};
          const name = personal?.farmer_name || personal?.name || reg?.name || 'Unknown';

          const farms = normalizeArray(reg?.farmer_farm_details || reg?.farm_details || reg?.land_parcels);
          const primaryFarm = farms[0] || {};

          let latitude: number | null = null;
          let longitude: number | null = null;
          const field_area_ha =
            primaryFarm?.area_unit_ha ||
            (primaryFarm?.plot_area ? Number(primaryFarm.plot_area) / 10000 : null);

          const geom = primaryFarm?.farm_geometry || primaryFarm?.geometry || primaryFarm?.parcel_geometry || null;
          const geomCoords =
            geom && typeof geom === 'object' && 'coordinates' in geom
              ? (geom as { coordinates?: unknown }).coordinates
              : undefined;
          if (Array.isArray(geomCoords) && geomCoords.length > 0) {
            if (geom.type === 'Polygon' || geom.type === 'MultiPolygon') {
              try {
                const coords = geom.type === 'Polygon' ? geom.coordinates[0] : geom.coordinates[0][0];
                const sumLat = coords.reduce((sum: number, pt: number[]) => sum + Number(pt[1] || 0), 0);
                const sumLon = coords.reduce((sum: number, pt: number[]) => sum + Number(pt[0] || 0), 0);
                latitude = sumLat / coords.length;
                longitude = sumLon / coords.length;
              } catch {
                // Keep null; pipeline will handle validation downstream.
              }
            } else if (geom.type === 'Point') {
              longitude = Number(geom.coordinates[0]);
              latitude = Number(geom.coordinates[1]);
            }
          }

          const crops = normalizeArray(primaryFarm?.crop_details);
          const primaryCrop = crops[0] || {};
          const cropName = primaryCrop?.crop_name || primaryCrop?.crop || null;
          const sowingDate = primaryCrop?.sowing_date || primaryCrop?.season || null;

          out.push({
            farmer_id: String(farmerId),
            name,
            latitude,
            longitude,
            geometry: geom,
            field_area_ha,
            crop: cropName,
            sowing_date: sowingDate,
            state_lgd_code: primaryFarm?.state_lgd_code || null,
            farmer_benefits: {
              pm_kisan_enrolled: !!(reg?.pm_kisan_enrolled || reg?.farmer_benefits?.pm_kisan_enrolled),
              has_crop_insurance: !!(reg?.crop_insurance_details || reg?.farmer_benefits?.has_crop_insurance),
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
    };

    const payloads = parsePayloads(agristack_response);
    let farmersToInsert = payloads.flatMap(extractFarmersFromPayload);

    if (farmersToInsert.length === 0) {
      // ACK-only path: look up async webhook payload by correlation_id
      const correlationIds = extractCorrelationIds(payloads);
      const { db } = await connectToDatabase();
      const webhookCollections = [
        db.collection('webhook_farmers_responses'),
        db.collection('webhook_responses'),
        db.collection('webhook_kdss_responses'),
      ];

      const webhookDocs: any[] = [];
      if (correlationIds.length > 0) {
        const escaped = correlationIds.map((id) =>
          id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        );
        for (const col of webhookCollections) {
          const docs = await col.find({
            $or: [
              { 'body.message.correlation_id': { $in: correlationIds } },
              { 'body.data.message.correlation_id': { $in: correlationIds } },
              { 'body.message.ack.correlation_id': { $in: correlationIds } },
              { 'body.data.message.ack.correlation_id': { $in: correlationIds } },
              { 'body.header.correlation_id': { $in: correlationIds } },
              { 'body.correlation_id': { $in: correlationIds } },
              ...escaped.map((id) => ({ rawBody: { $regex: id } })),
            ],
          }).sort({ receivedAt: -1 }).limit(10).toArray();
          webhookDocs.push(...docs);
        }
      }

      // Do NOT fall back to unrelated latest webhooks — that can save the wrong farmer.
      for (const doc of webhookDocs) {
        farmersToInsert = farmersToInsert.concat(
          extractFarmersFromPayload(doc?.body),
          extractFarmersFromPayload(doc?.body?.data),
          extractFarmersFromPayload(doc?.body?.message)
        );
      }
    }

    if (farmersToInsert.length === 0) {
      const correlationIds = extractCorrelationIds(payloads);
      const lambdaHint = process.env.AGRISTACK_PROXY_URL
        ? 'Confirm NEXT_PUBLIC_APP_DOMAIN points at the Mumbai Lambda webhook base, wait ~30–60s, open Webhook Responses, use Save to Platform there, then retry.'
        : 'Confirm sender_uri / NEXT_PUBLIC_APP_DOMAIN is reachable, wait ~30–60s, refresh Webhook Responses, then retry Save (or Save from the Webhook tab).';
      return NextResponse.json({
        error: correlationIds.length
          ? `Seek ACK only so far. No matching webhook farmer payload for correlation_id=${correlationIds.join(', ')}. ${lambdaHint}`
          : 'No farmer records found yet. Seek returned ACK only; wait for webhook callback, then Save from Webhook Responses or retry Save to Platform.',
      }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const collection = db.collection(COLLECTION);
    const farmerFarms = db.collection(FARMER_FARMS);

    const insertedIds: string[] = [];
    const created: string[] = [];
    const updated: string[] = [];
    const errors = [];

    // Upsert each farmer to ensure uniqueness by farmer_id (scoped to this user on mirror)
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

        // created_at must only appear in $setOnInsert — conflict if also in $set
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

        // Mirror into farmer_farms so home history lists AgriStack ingest for this user
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
          ...ownerFilter(jwtPayload),
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
        console.error('ingest-farmer upsert failed:', err);
        errors.push({ id: doc.farmer_id, error: err instanceof Error ? err.message : 'Unknown' });
      }
    }

    const parts: string[] = [];
    if (created.length) parts.push(`created ${created.length}`);
    if (updated.length) parts.push(`updated ${updated.length}`);
    const plotCounts = farmersToInsert.map((d) => ({
      farmer_id: d.farmer_id,
      n_plots: Array.isArray(d.farms) ? d.farms.length : 0,
      n_included: Array.isArray(d.farms)
        ? d.farms.filter((f: { included_in_assessment?: boolean }) => f.included_in_assessment !== false)
            .length
        : 0,
    }));
    const totalPlots = plotCounts.reduce((s, p) => s + p.n_plots, 0);

    if (insertedIds.length === 0) {
      return NextResponse.json(
        {
          success: false,
          error:
            errors[0]?.error ||
            'Failed to save farmers to MongoDB. Check server logs for details.',
          farmer_ids: [],
          errors,
          plot_counts: plotCounts,
        },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message:
        `Successfully ingested ${insertedIds.length} farmer(s)` +
        (parts.length ? ` (${parts.join(', ')})` : '') +
        (totalPlots ? ` · ${totalPlots} plot(s) stored` : ''),
      farmer_ids: insertedIds,
      created,
      updated,
      plot_counts: plotCounts,
      errors: errors.length > 0 ? errors : undefined,
    });

  } catch (error) {
    console.error('Ingest API Error:', error);
    return NextResponse.json({ 
      error: error instanceof Error ? error.message : 'Internal server error' 
    }, { status: 500 });
  }
}
