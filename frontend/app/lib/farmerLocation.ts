import { lookupDistrictName, lookupStateName } from './india_lgd_data';
import { farmerAssessHref, farmerResultsHref } from './farmerRoutes';

export type NamedPlace = { name?: string | null; lgd_code?: string | null } | null;

export type FarmerPlotSummary = {
  farm_id?: string;
  farm_name?: string;
  primary_crop?: string | null;
  area_ha?: number | null;
};

export type FarmerListItem = {
  _id: string;
  farmer_name?: string;
  phone?: string | null;
  agristack_farmer_id?: string | null;
  pipeline_farmer_id: string;
  source?: string;
  has_assessment?: boolean;
  latest_assessment_date?: string | Date | null;
  index_score?: number | null;
  risk_category?: string | null;
  location?: {
    state?: NamedPlace;
    district?: NamedPlace;
    taluka?: NamedPlace;
    village?: NamedPlace;
  };
  farms?: FarmerPlotSummary[];
  created_at?: string | Date;
  updated_at?: string | Date;
};

export const UNKNOWN_LOCATION_KEY = 'Unknown location';

export function pipelineIdOf(f: FarmerListItem): string {
  return f.pipeline_farmer_id || f.agristack_farmer_id || f._id;
}

export function placeName(place: NamedPlace | null | undefined): string | null {
  const n = place?.name?.trim();
  return n || null;
}

export function resolvePlaceName(
  place: NamedPlace,
  fallbackName?: string | null,
  lookup?: (code: string) => string | null
): string | null {
  const named = (place?.name || fallbackName || '').trim();
  if (named) return named;
  const code = place?.lgd_code ? String(place.lgd_code).trim() : '';
  if (code && lookup) {
    const resolved = lookup(code);
    if (resolved?.trim()) return resolved.trim();
  }
  return null;
}

export function resolveStateName(opts: {
  place?: NamedPlace;
  name?: string | null;
  lgd_code?: string | null;
}): string | null {
  return resolvePlaceName(
    opts.place ?? { name: opts.name, lgd_code: opts.lgd_code },
    opts.name,
    lookupStateName
  );
}

export function resolveDistrictName(opts: {
  place?: NamedPlace;
  name?: string | null;
  lgd_code?: string | null;
}): string | null {
  return resolvePlaceName(
    opts.place ?? { name: opts.name, lgd_code: opts.lgd_code },
    opts.name,
    lookupDistrictName
  );
}

export function locationGroupKey(state: string | null, district: string | null): string {
  if (district && state) return `${state} · ${district}`;
  if (district) return district;
  if (state) return state;
  return UNKNOWN_LOCATION_KEY;
}

export function farmerLocationParts(f: FarmerListItem): {
  state: string | null;
  district: string | null;
  village: string | null;
  key: string;
} {
  const state = placeName(f.location?.state);
  const district = placeName(f.location?.district);
  const village = placeName(f.location?.village);
  return { state, district, village, key: locationGroupKey(state, district) };
}

export type LocationBucket = {
  key: string;
  state: string | null;
  district: string | null;
  farmerCount: number;
  farmCount: number;
  assessedCount: number;
  areaHa: number;
};

export const UNKNOWN_DISTRICT_KEY = 'Unknown district';

function sortBuckets(buckets: LocationBucket[]): LocationBucket[] {
  return [...buckets].sort((a, b) => {
    const aUnknown = a.key === UNKNOWN_LOCATION_KEY || a.key === UNKNOWN_DISTRICT_KEY;
    const bUnknown = b.key === UNKNOWN_LOCATION_KEY || b.key === UNKNOWN_DISTRICT_KEY;
    if (aUnknown && !bUnknown) return 1;
    if (bUnknown && !aUnknown) return -1;
    return b.farmerCount - a.farmerCount || a.key.localeCompare(b.key);
  });
}

function accumulateBuckets(
  farmers: FarmerListItem[],
  keyOf: (parts: ReturnType<typeof farmerLocationParts>) => {
    key: string;
    state: string | null;
    district: string | null;
  }
): LocationBucket[] {
  const map = new Map<string, LocationBucket>();
  for (const f of farmers) {
    const parts = farmerLocationParts(f);
    const { key, state, district } = keyOf(parts);
    const farms = Array.isArray(f.farms) ? f.farms.length : 0;
    const area = (f.farms || []).reduce(
      (sum, p) => sum + (typeof p.area_ha === 'number' ? p.area_ha : 0),
      0
    );
    const existing = map.get(key);
    if (existing) {
      existing.farmerCount += 1;
      existing.farmCount += farms;
      existing.assessedCount += f.has_assessment ? 1 : 0;
      existing.areaHa += area;
    } else {
      map.set(key, {
        key,
        state,
        district,
        farmerCount: 1,
        farmCount: farms,
        assessedCount: f.has_assessment ? 1 : 0,
        areaHa: area,
      });
    }
  }
  return sortBuckets([...map.values()]);
}

export function groupFarmersByLocation(farmers: FarmerListItem[]): LocationBucket[] {
  return accumulateBuckets(farmers, (parts) => ({
    key: parts.key,
    state: parts.state,
    district: parts.district,
  }));
}

/** Top-level filter: one bar per state. */
export function groupFarmersByState(farmers: FarmerListItem[]): LocationBucket[] {
  return accumulateBuckets(farmers, (parts) => ({
    key: parts.state || UNKNOWN_LOCATION_KEY,
    state: parts.state,
    district: null,
  }));
}

/** Second-level filter: districts inside a selected state. */
export function groupFarmersByDistrict(
  farmers: FarmerListItem[],
  stateKey: string
): LocationBucket[] {
  const inState = farmers.filter((f) => {
    const parts = farmerLocationParts(f);
    return (parts.state || UNKNOWN_LOCATION_KEY) === stateKey;
  });
  return accumulateBuckets(inState, (parts) => ({
    key: parts.district || UNKNOWN_DISTRICT_KEY,
    state: parts.state,
    district: parts.district,
  }));
}

export function cropList(f: FarmerListItem): string[] {
  return [
    ...new Set((f.farms || []).map((x) => x.primary_crop).filter((c): c is string => !!c)),
  ];
}

export function totalAreaHa(f: FarmerListItem): number | null {
  const areas = (f.farms || [])
    .map((p) => p.area_ha)
    .filter((n): n is number => typeof n === 'number' && Number.isFinite(n));
  if (!areas.length) return null;
  return areas.reduce((a, b) => a + b, 0);
}

export function sourceLabel(f: FarmerListItem): string {
  if (f.source === 'agristack_ingest' || f.agristack_farmer_id) return 'AgriStack';
  return 'Journey';
}

export function formatShortDate(value: string | Date | null | undefined): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleDateString('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return '—';
  }
}

export { farmerAssessHref, farmerResultsHref };
