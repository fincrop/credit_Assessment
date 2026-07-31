import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';
import {
  buildClusteredFarmFields,
  centroidFromPlotGeometry,
} from '../../lib/farmerParcelCluster';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const COLLECTION = 'farm_info';

export async function POST(req: NextRequest) {
  try {
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
            const fd = reg.FarmerData;
            const farmerId = fd.farmer_id;
            if (!farmerId) continue;
            const name = fd.farmer_name || fd.name || 'Unknown';
            const lands = normalizeArray(reg.land_data).filter(Boolean) as Record<string, unknown>[];
            let primaryFarm = (lands[0] || {}) as Record<string, unknown>;
            let geom: Record<string, unknown> | null = null;
            let latitude: number | null = null;
            let longitude: number | null = null;
            let field_area_ha: number | null = null;
            let parcel_ingest_stats: ReturnType<
              typeof buildClusteredFarmFields
            >['ingest_stats'] | null = null;

            if (lands.length > 0) {
              const clustered = buildClusteredFarmFields(lands);
              primaryFarm = (clustered.clusteredParcels[0] || primaryFarm) as Record<string, unknown>;
              parcel_ingest_stats = clustered.ingest_stats;
              geom =
                (clustered.geometry as Record<string, unknown> | null) ||
                ((primaryFarm.plot_geometry || primaryFarm.farm_geometry) as Record<
                  string,
                  unknown
                > | null) ||
                null;
              field_area_ha =
                clustered.field_area_ha != null && clustered.field_area_ha > 0
                  ? clustered.field_area_ha
                  : null;
              if (
                clustered.centroid?.latitude != null &&
                clustered.centroid.longitude != null
              ) {
                latitude = clustered.centroid.latitude;
                longitude = clustered.centroid.longitude;
              }
            }

            const legacyExtent =
              primaryFarm.owner_extent != null && primaryFarm.owner_extent !== ''
                ? Number(primaryFarm.owner_extent)
                : null;
            let legacyHa =
              legacyExtent != null && Number.isFinite(legacyExtent) ? legacyExtent : null;
            if (
              legacyHa != null &&
              primaryFarm.area_unit &&
              String(primaryFarm.area_unit).toLowerCase().includes('acre')
            ) {
              legacyHa *= 0.404686;
            }
            if (field_area_ha == null && legacyHa != null) {
              field_area_ha = legacyHa;
            }

            type GeoJsonLike = { type?: string; coordinates?: unknown } | null;
            const gForCe = geom as GeoJsonLike;
            const gCoords = gForCe?.coordinates;
            const hasCoordArray =
              Array.isArray(gCoords) && gCoords.length > 0;

            if (gForCe && hasCoordArray && (latitude == null || longitude == null)) {
              if (gForCe.type === 'Polygon' || gForCe.type === 'MultiPolygon') {
                const cg = centroidFromPlotGeometry({
                  type: gForCe.type,
                  coordinates: gCoords,
                });
                if (cg) {
                  latitude = cg.lat;
                  longitude = cg.lon;
                }
                try {
                  const coords =
                    gForCe.type === 'Polygon'
                      ? (gCoords as number[][][])[0]
                      : (gCoords as number[][][][])[0]?.[0];
                  if (
                    coords?.length &&
                    (latitude == null || longitude == null)
                  ) {
                    const sumLat = coords.reduce(
                      (sum: number, pt: number[]) => sum + Number(pt[1] || 0),
                      0
                    );
                    const sumLon = coords.reduce(
                      (sum: number, pt: number[]) => sum + Number(pt[0] || 0),
                      0
                    );
                    latitude = sumLat / coords.length;
                    longitude = sumLon / coords.length;
                  }
                } catch {
                  /* ignore */
                }
              } else if (gForCe.type === 'Point') {
                const pts = gCoords as number[];
                longitude = Number(pts[0]);
                latitude = Number(pts[1]);
              }
            }

            out.push({
              farmer_id: String(farmerId),
              name,
              latitude,
              longitude,
              geometry: geom,
              field_area_ha,
              parcel_ingest_stats: parcel_ingest_stats || undefined,
              crop: null,
              sowing_date: null,
              state_lgd_code: fd.state_lgd_code ?? reg.state_lgd_code ?? primaryFarm.state_lgd_code ?? null,
              farmer_benefits: {
                pm_kisan_enrolled: false,
                has_crop_insurance: false,
              },
              source: 'agristack_ingest',
              status: 'active',
              created_at: new Date(),
              updated_at: new Date(),
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
        for (const col of webhookCollections) {
          const docs = await col.find({
            $or: [
              { 'body.message.correlation_id': { $in: correlationIds } },
              { 'body.data.message.correlation_id': { $in: correlationIds } },
              { 'body.message.ack.correlation_id': { $in: correlationIds } },
              { 'body.data.message.ack.correlation_id': { $in: correlationIds } },
            ],
          }).sort({ receivedAt: -1 }).limit(10).toArray();
          webhookDocs.push(...docs);
        }
      }

      // Do NOT fall back to unrelated latest webhooks — that can save the wrong farmer.
      for (const doc of webhookDocs) {
        farmersToInsert = farmersToInsert.concat(extractFarmersFromPayload(doc?.body));
      }
    }

    if (farmersToInsert.length === 0) {
      const correlationIds = extractCorrelationIds(payloads);
      return NextResponse.json({
        error: correlationIds.length
          ? `Seek ACK only so far. No webhook payload yet for correlation_id=${correlationIds.join(', ')}. Keep Cloudflare tunnel running, confirm sender_uri uses NEXT_PUBLIC_APP_DOMAIN, wait ~30–60s, refresh Webhook Responses, then retry Save.`
          : 'No farmer records found yet. Seek returned ACK only; wait for webhook callback, then retry Save to Platform.',
      }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const collection = db.collection(COLLECTION);

    const insertedIds: string[] = [];
    const created: string[] = [];
    const updated: string[] = [];
    const errors = [];

    // Upsert each farmer to ensure uniqueness by farmer_id
    for (const doc of farmersToInsert) {
      try {
        const result = await collection.updateOne(
          { farmer_id: doc.farmer_id },
          { $set: doc },
          { upsert: true }
        );
        insertedIds.push(doc.farmer_id);
        if (result.upsertedCount > 0) created.push(doc.farmer_id);
        else updated.push(doc.farmer_id);
      } catch (err) {
        errors.push({ id: doc.farmer_id, error: err instanceof Error ? err.message : 'Unknown' });
      }
    }

    const parts: string[] = [];
    if (created.length) parts.push(`created ${created.length}`);
    if (updated.length) parts.push(`updated ${updated.length}`);

    return NextResponse.json({
      success: true,
      message: `Successfully ingested ${insertedIds.length} farmer(s)` +
        (parts.length ? ` (${parts.join(', ')})` : ''),
      farmer_ids: insertedIds,
      created,
      updated,
      errors: errors.length > 0 ? errors : undefined
    });

  } catch (error) {
    console.error('Ingest API Error:', error);
    return NextResponse.json({ 
      error: error instanceof Error ? error.message : 'Internal server error' 
    }, { status: 500 });
  }
}
