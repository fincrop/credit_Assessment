/**
 * Shared farm_info builder for AgriStack ingest + farmer journey.
 * One doc per farmer_id with farms[] (all plots) + envelope = largest owned plot.
 */

import {
  buildClusteredFarmFields,
  centroidFromPlotGeometry,
  parseOwnerExtentHa,
} from './farmerParcelCluster';
import { lookupDistrictName, lookupStateName } from './india_lgd_data';

export type FarmInfoGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }
  | Record<string, unknown>;

export type FarmInfoPlot = {
  farm_id: string;
  farm_name?: string | null;
  survey_number?: string | null;
  sub_survey_number?: string | null;
  village_lgd_code?: string | null;
  district_lgd_code?: string | null;
  sub_district_lgd_code?: string | null;
  state_lgd_code?: string | null;
  area_ha: number;
  area_unit?: string | null;
  geometry: FarmInfoGeometry | null;
  centroid?: { lat: number; lng: number } | null;
  primary_crop?: string | null;
  sowing_date?: string | null;
  land_usage_type?: string | null;
  owner_name_ror?: string | null;
  joint_owners?: unknown[];
  /** Prefer true for owned plots; clustering only marks deliberate excludes. */
  included_in_assessment: boolean;
  /**
   * Journey self-asserted boundaries default true (optimistic full tenure).
   * AgriStack should derive from ROR when possible.
   */
  is_ror_owner: boolean | null;
  ownership_share?: number | null;
};

export type FarmInfoDocument = {
  farmer_id: string;
  name: string;
  mobile?: string | null;
  latitude: number | null;
  longitude: number | null;
  geometry: FarmInfoGeometry | null;
  field_area_ha: number | null;
  crop?: string | null;
  sowing_date?: string | null;
  state_lgd_code?: string | null;
  district_lgd_code?: string | null;
  state?: string | null;
  district?: string | null;
  village?: string | null;
  farmer_benefits: {
    pm_kisan_enrolled: boolean | null;
    has_crop_insurance: boolean | null;
  };
  farms: FarmInfoPlot[];
  farmer_profile?: Record<string, unknown>;
  parcel_ingest_stats?: Record<string, unknown>;
  source: string;
  status: string;
  created_by?: string;
  user_id?: string;
  updated_at: Date;
  created_at?: Date;
};

function centroidOfGeom(
  geom: FarmInfoGeometry | null | undefined
): { lat: number; lng: number } | null {
  if (!geom) return null;
  const c = centroidFromPlotGeometry(geom as { type?: string; coordinates?: unknown });
  if (!c) return null;
  return { lat: c.lat, lng: c.lon };
}

function largestOwnedEnvelope(farms: FarmInfoPlot[]): {
  geometry: FarmInfoGeometry | null;
  latitude: number | null;
  longitude: number | null;
  field_area_ha: number | null;
  crop: string | null;
  sowing_date: string | null;
} {
  const owned = farms.filter(
    (f) => f.included_in_assessment && (f.area_ha || 0) >= 0 && f.geometry
  );
  const pool = owned.length ? owned : farms.filter((f) => f.geometry);
  if (!pool.length) {
    return {
      geometry: null,
      latitude: null,
      longitude: null,
      field_area_ha: null,
      crop: null,
      sowing_date: null,
    };
  }
  const best = [...pool].sort((a, b) => (b.area_ha || 0) - (a.area_ha || 0))[0];
  const c = best.centroid || centroidOfGeom(best.geometry);
  return {
    geometry: best.geometry,
    latitude: c?.lat ?? null,
    longitude: c?.lng ?? null,
    field_area_ha: best.area_ha > 0 ? best.area_ha : null,
    crop: best.primary_crop || null,
    sowing_date: best.sowing_date || null,
  };
}

function inferIsRorOwner(
  farmerName: string,
  ownerNameRor: string | null | undefined
): boolean | null {
  if (!ownerNameRor || !farmerName) return null;
  const a = farmerName.trim().toLowerCase();
  const b = String(ownerNameRor).trim().toLowerCase();
  if (!a || !b) return null;
  return a.includes(b) || b.includes(a);
}

/** Journey polygons → FarmInfoPlot[] (is_ror_owner defaults true — self-asserted). */
export function normalizeJourneyFarms(
  farms: Array<{
    farm_id?: string;
    farm_name?: string;
    boundary?: { type?: string; coordinates?: number[][][] };
    area_ha?: number;
    centroid?: { lat?: number; lng?: number };
    primary_crop?: string;
    sowing_date?: string | null;
  }>,
  opts: {
    district_lgd_code?: string | null;
    state_lgd_code?: string | null;
    village_lgd_code?: string | null;
  }
): FarmInfoPlot[] {
  const district = opts.district_lgd_code || null;
  return farms.map((f, i) => {
    const boundary =
      f.boundary && f.boundary.type === 'Polygon' && Array.isArray(f.boundary.coordinates)
        ? (f.boundary as FarmInfoGeometry)
        : null;
    const centroid =
      f.centroid?.lat != null && f.centroid?.lng != null
        ? { lat: Number(f.centroid.lat), lng: Number(f.centroid.lng) }
        : centroidOfGeom(boundary);
    return {
      farm_id: String(f.farm_id || `journey_${i + 1}`),
      farm_name: f.farm_name || null,
      district_lgd_code: district,
      state_lgd_code: opts.state_lgd_code || null,
      village_lgd_code: opts.village_lgd_code || null,
      area_ha: Number(f.area_ha) || 0,
      area_unit: 'Hectare',
      geometry: boundary,
      centroid,
      primary_crop: f.primary_crop || null,
      sowing_date: f.sowing_date || null,
      included_in_assessment: true,
      // Optimistic: journey plots are self-asserted ownership (full tenure weight).
      is_ror_owner: true,
      joint_owners: [],
    };
  });
}

/** AgriStack land_data[] → FarmInfoPlot[] (all plots kept). */
export function normalizeAgriStackLands(
  landData: Record<string, unknown>[],
  farmerData: Record<string, unknown>,
  opts?: { includeAllOwned?: boolean }
): { farms: FarmInfoPlot[]; parcel_ingest_stats?: Record<string, unknown> } {
  const includeAll = opts?.includeAllOwned !== false;
  const farmerName = String(farmerData.farmer_name || farmerData.name || '');
  const topDistrict =
    (farmerData.district_lgd_code as string) ||
    (landData[0]?.district_lgd_code as string) ||
    null;
  const topState =
    (farmerData.state_lgd_code as string) ||
    (landData[0]?.state_lgd_code as string) ||
    null;

  let includedIds = new Set<string>();
  let stats: Record<string, unknown> | undefined;
  if (!includeAll && landData.length > 1) {
    const clustered = buildClusteredFarmFields(landData);
    stats = clustered.ingest_stats as unknown as Record<string, unknown>;
    for (const p of clustered.clusteredParcels) {
      if (p.farm_id) includedIds.add(String(p.farm_id));
    }
  } else {
    for (const p of landData) {
      if (p.farm_id) includedIds.add(String(p.farm_id));
    }
    stats = {
      parcels_total: landData.length,
      parcels_clustered: landData.length,
      parcels_excluded: 0,
      owner_extent_sum_ha: landData.reduce(
        (s, p) => s + parseOwnerExtentHa(p as { owner_extent?: string; area_unit?: string }),
        0
      ),
    };
  }

  const farms: FarmInfoPlot[] = landData.map((p, i) => {
    const geom = (p.plot_geometry || p.farm_geometry || null) as FarmInfoGeometry | null;
    const area = parseOwnerExtentHa(p as { owner_extent?: string; area_unit?: string });
    const farmId = String(p.farm_id || `plot_${i + 1}`);
    const ownerRor = p.owner_name_ror != null ? String(p.owner_name_ror) : null;
    const joints = Array.isArray(p.joint_owners) ? p.joint_owners : [];
    return {
      farm_id: farmId,
      farm_name: (p.farm_name as string) || farmId,
      survey_number: p.survey_number != null ? String(p.survey_number) : null,
      sub_survey_number: p.sub_survey_number != null ? String(p.sub_survey_number) : null,
      village_lgd_code:
        (p.village_lgd_code as string) ||
        (farmerData.village_lgd_code as string) ||
        null,
      district_lgd_code: (p.district_lgd_code as string) || topDistrict,
      sub_district_lgd_code: (p.sub_district_lgd_code as string) || null,
      state_lgd_code: (p.state_lgd_code as string) || topState,
      area_ha: area,
      area_unit: (p.area_unit as string) || 'Hectare',
      geometry: geom,
      centroid: centroidOfGeom(geom),
      primary_crop: null,
      sowing_date: null,
      land_usage_type: p.land_usage_type != null ? String(p.land_usage_type) : null,
      owner_name_ror: ownerRor,
      joint_owners: joints,
      included_in_assessment: includedIds.has(farmId) || includeAll,
      is_ror_owner: inferIsRorOwner(farmerName, ownerRor) ?? (joints.length ? false : true),
      ownership_share: null,
    };
  });

  return { farms, parcel_ingest_stats: stats };
}

export function buildFarmInfoDocument(params: {
  farmer_id: string;
  name: string;
  mobile?: string | null;
  farms: FarmInfoPlot[];
  farmer_benefits?: {
    pm_kisan_enrolled?: boolean | null;
    has_crop_insurance?: boolean | null;
  };
  farmer_profile?: Record<string, unknown>;
  parcel_ingest_stats?: Record<string, unknown>;
  state_lgd_code?: string | null;
  district_lgd_code?: string | null;
  state?: string | null;
  district?: string | null;
  village?: string | null;
  source: string;
  created_by?: string;
  user_id?: string;
  now?: Date;
}): FarmInfoDocument {
  const now = params.now || new Date();
  const envelope = largestOwnedEnvelope(params.farms);
  const district =
    params.district_lgd_code ||
    params.farms.find((f) => f.district_lgd_code)?.district_lgd_code ||
    null;
  const stateCode = params.state_lgd_code || null;
  const stateName =
    (params.state && String(params.state).trim()) || lookupStateName(stateCode);
  const districtName =
    (params.district && String(params.district).trim()) || lookupDistrictName(district);

  return {
    farmer_id: params.farmer_id,
    name: params.name,
    mobile: params.mobile ?? null,
    latitude: envelope.latitude,
    longitude: envelope.longitude,
    geometry: envelope.geometry,
    field_area_ha: envelope.field_area_ha,
    crop: envelope.crop,
    sowing_date: envelope.sowing_date,
    state_lgd_code: stateCode,
    district_lgd_code: district,
    state: stateName || null,
    district: districtName || null,
    village: params.village || null,
    farmer_benefits: {
      pm_kisan_enrolled: params.farmer_benefits?.pm_kisan_enrolled ?? null,
      has_crop_insurance: params.farmer_benefits?.has_crop_insurance ?? null,
    },
    farms: params.farms,
    farmer_profile: params.farmer_profile,
    parcel_ingest_stats: params.parcel_ingest_stats,
    source: params.source,
    status: 'active',
    created_by: params.created_by,
    user_id: params.user_id,
    updated_at: now,
  };
}
