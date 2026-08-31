/** Shared types for the Farmer Journey wizard */

export interface LgdSelection {
  lgd_code: string;
  name: string;
}

export interface FarmerLocation {
  state: LgdSelection | null;
  district: LgdSelection | null;
  taluka: LgdSelection | null;
  village: LgdSelection | null;
  katha_number: string;
  /** Map center hint from geolocation / geocode */
  mapCenter?: { lat: number; lng: number } | null;
}

export interface FarmPolygon {
  farm_id: string;
  farm_name: string;
  farm_number: string;
  boundary: {
    type: 'Polygon';
    coordinates: number[][][];
  };
  area_ha: number;
  centroid: { lat: number; lng: number };
  primary_crop: string;
  sowing_date: string;
  color?: string;
}

export interface HistoricalSeason {
  year: string;
  season: string;
  crop: string;
  yield_estimate_kg_ha: number | '';
}

/** Tri-state benefit flags — null means unknown (never coerce with !!). */
export type BenefitTriState = boolean | null;

export interface FarmerIdentity {
  farmer_name: string;
  phone: string;
  language: string;
  agristack_farmer_id: string;
  pm_kisan_enrolled: BenefitTriState;
  has_crop_insurance: BenefitTriState;
}

export interface FarmerExtras {
  historical_data: HistoricalSeason[];
  irrigation_type: string;
  soil_type: string;
  pm_kisan_enrolled: BenefitTriState;
  has_crop_insurance: BenefitTriState;
  notes: string;
}

export const CROP_OPTIONS = [
  'Rice', 'Wheat', 'Cotton', 'Sugarcane', 'Maize', 'Soybean',
  'Groundnut', 'Mustard', 'Chickpea', 'Pigeon Pea', 'Sorghum',
  'Pearl Millet', 'Vegetables', 'Other',
] as const;

export const LANGUAGE_OPTIONS = ['English', 'Hindi', 'Marathi', 'Telugu', 'Tamil', 'Kannada', 'Gujarati', 'Bengali', 'Punjabi'] as const;

export const IRRIGATION_OPTIONS = ['Rainfed', 'Canal', 'Drip', 'Sprinkler', 'Tube Well', 'Mixed'] as const;

export const SEASON_OPTIONS = ['Kharif', 'Rabi', 'Zaid', 'Annual'] as const;

export const FARM_COLORS = [
  '#22c55e', '#3b82f6', '#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#ef4444', '#84cc16',
];

/** Approximate polygon area in hectares from GeoJSON ring [lng, lat][] */
export function polygonAreaHa(ring: number[][]): number {
  if (!ring || ring.length < 3) return 0;
  const R = 6371000; // meters
  let area = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[i + 1];
    area +=
      ((lng2 * Math.PI) / 180 - (lng1 * Math.PI) / 180) *
      (2 + Math.sin((lat1 * Math.PI) / 180) + Math.sin((lat2 * Math.PI) / 180));
  }
  area = (Math.abs(area) * R * R) / 2;
  return area / 10000; // m² → ha
}

export function polygonCentroid(ring: number[][]): { lat: number; lng: number } {
  if (!ring?.length) return { lat: 20.5, lng: 78.9 };
  let lng = 0;
  let lat = 0;
  const n = ring.length - (ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1] ? 1 : 0);
  for (let i = 0; i < n; i++) {
    lng += ring[i][0];
    lat += ring[i][1];
  }
  return { lat: lat / n, lng: lng / n };
}
