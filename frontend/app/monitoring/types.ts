import type { AreaOfInterest } from '../classification/types';

export type MonitorStage = 'queued' | 'observing' | 'analysing' | 'publishing' | 'complete' | 'failed';

export const STAGE_LABELS: Record<MonitorStage, string> = {
  queued: 'Queued',
  observing: 'Collecting satellite and weather',
  analysing: 'Scoring the season',
  publishing: 'Building the map',
  complete: 'Complete',
  failed: 'Failed',
};

export interface MonitorInputs {
  name: string;
  crop: string;
  confidence: number;
  season: string;
  as_of: string;
  sowing_date: string;
  district_yield_t_ha: string;
  cluster_id: string;
  village: string;
  classification_job_id: string;
  source_field_id: string;
}

export interface MonitorProgress {
  job_id: string;
  stage: MonitorStage;
  percent: number | null;
  message?: string;
  error?: string;
}

export interface MonitorZone {
  zone_id: string;
  kind: string;
  crop?: string;
  pixel_count?: number;
  area_share?: number;
  greenup?: string | null;
  split_reason?: string;
  indices?: Array<{ date: string; sensor: string }>;
  sowing?: {
    known?: boolean;
    date?: string | null;
    early?: string | null;
    late?: string | null;
    confidence?: number;
    sources?: string[];
    note?: string;
  };
  harvest?: {
    observed?: boolean;
    date?: string | null;
    early?: string | null;
    late?: string | null;
    picks?: string[];
    in_season?: boolean | null;
    note?: string;
  };
  progress?: {
    stage?: string;
    tau?: number;
    emergence?: string | null;
    peak?: string | null;
    harvest?: string | null;
    harvest_window?: Array<string | null>;
    duration_days?: number | null;
    duration_outlier?: boolean;
    note?: string;
  };
  yield?: {
    t_ha?: number;
    low?: number;
    high?: number;
    reference_pool?: string;
    retention?: number;
    estimators?: Record<string, number>;
    note?: string;
  } | null;
  irrigation_dates?: string[];
  intervals?: Array<{
    date: string;
    kind: string;
    stage?: string;
    tau?: number;
    cover?: number;
    water?: number;
    biomass_kg_ha?: number;
    uncertainty?: number;
    stress?: { type?: string; stressed_fraction?: number } | null;
    nitrogen?: { score?: number; band?: string } | null;
  }>;
}

export interface MonitoringResult {
  job_id: string;
  name?: string;
  crop: string;
  season?: string;
  as_of?: string;
  confidence?: number;
  typed?: boolean;
  zone_count?: number;
  farm_count?: number;
  reference_pool?: string | null;
  farms?: Array<{
    field_id: string;
    crop: string;
    area_ha?: number | null;
    sowing_date?: string | null;
    harvest_date?: string | null;
    yield_t_ha?: number | null;
    stress?: string | null;
  }>;
  zones: MonitorZone[];
  fields: GeoJSON.FeatureCollection;
  exclusion_reason?: string | null;
  cluster_summary?: {
    cluster_id?: string | null;
    village?: string | null;
    farm_count?: number;
    excluded_count?: number;
    mean_yield_t_ha?: number | null;
    sowing_earliest?: string | null;
    sowing_latest?: string | null;
    stress_counts?: Record<string, number>;
    cropland_fraction?: number | null;
  } | null;
  skipped?: Array<{ field_id?: string; crop?: string; note?: string }>;
  limits?: string[];
  models?: Record<string, string>;
}

export interface MonitorHistoryItem {
  job_id: string;
  stage: string;
  error?: string | null;
  created_at?: string;
  finished_at?: string;
  name: string;
  crop: string;
  season: string;
  as_of: string;
  zone_count: number | null;
  total_area_ha: number | null;
  areas: AreaOfInterest[];
  inputs: Partial<MonitorInputs>;
}

export type MonitorDownload = 'geojson' | 'csv' | 'json' | 'shapefile' | 'geotiff' | 'png';

export const DOWNLOAD_OPTIONS: { format: MonitorDownload; label: string; hint: string }[] = [
  { format: 'geojson', label: 'GeoJSON', hint: 'Zone map with sowing, stress and yield' },
  { format: 'shapefile', label: 'Shapefile (.zip)', hint: 'Zones for QGIS / ArcGIS, plus the CSV' },
  { format: 'geotiff', label: 'GeoTIFF', hint: 'Zone raster of the field' },
  { format: 'png', label: 'Map image (PNG)', hint: 'Zone map with legend' },
  { format: 'csv', label: 'Analytical CSV', hint: 'Zone summary and every observation' },
  { format: 'json', label: 'Full analysis (JSON)', hint: 'The complete monitoring document' },
];

/** Same colours the map layer and the PNG legend use. */
export const STRESS_COLORS: Record<string, string> = {
  Healthy: '#2E7D4F',
  'Nutrient Deficit': '#C9A227',
  'Crop Water Deficit': '#D97A34',
  'Tissue Damage': '#B4553A',
  'Sub-optimal Growth': '#7C5FA8',
  Unspecified: '#8F8779',
};

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
