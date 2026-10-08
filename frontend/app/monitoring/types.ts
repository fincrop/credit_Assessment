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

/* ---------------------------------------------------------------------------
 * Raster engine (result.engine === 'raster_v1')
 * ------------------------------------------------------------------------- */

export type RecordStatus =
  | 'confirmed'
  | 'provisional'
  | 'intercrop'
  | 'phenology_disagrees'
  | 'insufficient_evidence'
  | 'crop_unknown';

export const RECORD_STATUS_LABELS: Record<RecordStatus, string> = {
  confirmed: 'Confirmed',
  provisional: 'Provisional',
  intercrop: 'Intercrop',
  phenology_disagrees: 'Phenology disagrees',
  insufficient_evidence: 'Insufficient evidence',
  crop_unknown: 'Crop not named',
};

/** Badge classes: confirmed green, provisional amber, disagreement red, no evidence grey. */
export const RECORD_STATUS_STYLES: Record<RecordStatus, string> = {
  confirmed: 'border-emerald-300 bg-emerald-50 text-emerald-800',
  provisional: 'border-amber-300 bg-amber-50 text-amber-800',
  intercrop: 'border-sky-300 bg-sky-50 text-sky-800',
  phenology_disagrees: 'border-red-300 bg-red-50 text-red-800',
  insufficient_evidence: 'border-stone-300 bg-stone-100 text-stone-600',
  crop_unknown: 'border-stone-300 bg-stone-100 text-stone-600',
};

export type PixelBasis = 'interior' | 'inner' | 'full';

export interface FieldSowing {
  status?: 'estimated' | 'insufficient_evidence' | 'no_cue' | 'provided' | 'before_window';
  date?: string | null;
  p10?: string | null;
  p90?: string | null;
  sources?: string[];
  regime?: string | null;
  onset?: string | null;
  note?: string | null;
}

export interface FieldHarvest {
  observed?: boolean;
  date?: string | null;
  window?: { kind?: 'multi_pick' | 'single'; start?: string | null; end?: string | null } | null;
}

export type StressClass = 'healthy' | 'mild' | 'moderate' | 'severe' | 'establishing';

export interface FieldStress {
  status?: 'scored' | 'condition_unavailable';
  latest_date?: string | null;
  latest_class?: StressClass | string | null;
  latest_type?: string | null;
  latest_confirmed?: boolean | null;
  looks_scored?: number | null;
  looks_stressed?: number | null;
  confirmed_looks?: number | null;
  severity_index?: number | null;
  reference?: string | null;
}

export interface FieldYield {
  basis?: 'district_anchored' | 'index_only' | 'withheld' | 'insufficient_peers';
  yield_index?: number | null;
  index_p10?: number | null;
  index_p90?: number | null;
  yield_t_ha?: number | null;
  yield_p10?: number | null;
  yield_p90?: number | null;
  baseline_source?: string | null;
  label?: string | null;
  note?: string | null;
}

export type PhenologyCandidate = string | { crop?: string; ratio?: number; score?: number; error?: number };

export interface PhenologyCheck {
  checked?: boolean;
  claimed?: string | null;
  best_alternative?: string | null;
  candidates?: PhenologyCandidate[];
  ratio?: number | null;
  disagrees?: boolean;
  reason?: string | null;
}

/** One field as the raster engine reports it; every number comes from the same pixels as the maps. */
export interface FieldRecord {
  field_id: string;
  crop: string;
  status?: RecordStatus;
  area_ha?: number | null;
  confidence?: number | null;
  confidence_basis?: 'model' | 'reference_curve' | string | null;
  reference_note?: string | null;
  pixel_basis?: PixelBasis;
  n_interior_pixels?: number | null;
  low_resolution?: boolean;
  n_clear_looks?: number | null;
  last_clear_observation?: string | null;
  phenology_status?: string | null;
  sowing?: FieldSowing | null;
  das?: number | null;
  stage?: string | null;
  stage_if_alternative?: { crop?: string; stage?: string } | null;
  harvest?: FieldHarvest | null;
  peak?: string | null;
  stress?: FieldStress | null;
  yield?: FieldYield | null;
  phenology_check?: PhenologyCheck | null;
  qa_flags?: string[];
}

export type RasterProductKind =
  | 'ndvi'
  | 'ndre'
  | 'ndmi'
  | 'anomaly'
  | 'stress_class'
  | 'vh'
  | 'sowing_doy'
  | 'stage';

/** Display order and labels of the product picker. */
export const RASTER_PRODUCT_LABELS: Record<RasterProductKind, string> = {
  ndvi: 'NDVI',
  ndre: 'Red-edge (NDRE)',
  ndmi: 'Moisture (NDMI)',
  anomaly: 'Anomaly vs cohort',
  stress_class: 'Stress class',
  vh: 'Radar VH',
  sowing_doy: 'Sowing date',
  stage: 'Stage',
};

export interface RasterClassLegendEntry {
  value: number | string;
  label: string;
  color: string;
}

export interface RasterRampLegend {
  vmin: number;
  vmax: number;
  ramp: string[];
  units?: string | null;
}

export type RasterLegend = RasterClassLegendEntry[] | RasterRampLegend;

export interface RasterProduct {
  product: RasterProductKind | string;
  date: string | null;
  sensor?: string | null;
  clear_fraction?: number | null;
  /** "cog/<name>.tif" — relative to the job's raster directory. */
  cog?: string | null;
  /** "png/<name>.png" — already in Web Mercator for an image overlay. */
  png?: string | null;
  /** [[south, west], [north, east]] in lat/lon. */
  bounds?: [[number, number], [number, number]] | null;
  legend?: RasterLegend | null;
  no_data?: string | null;
  note?: string | null;
}

export interface MonsoonOnset {
  date?: string | null;
  cumulative_mm?: number | null;
  false_starts?: Array<string | { date?: string; note?: string }>;
  note?: string | null;
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
    confidence?: number | null;
    confidence_basis?: string | null;
    sowing_date?: string | null;
    harvest_date?: string | null;
    yield_t_ha?: number | null;
    stress?: string | null;
    status?: RecordStatus | null;
    sowing_p10?: string | null;
    sowing_p90?: string | null;
    stage?: string | null;
    yield_index?: number | null;
    qa_flags?: string[] | string | null;
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
    status_counts?: Record<string, number>;
    cropland_fraction?: number | null;
  } | null;
  skipped?: Array<{ field_id?: string; crop?: string; note?: string }>;
  limits?: string[];
  models?: Record<string, string>;
  /** "raster_v1" for the per-pixel engine; absent on older point-engine results. */
  engine?: string;
  records?: FieldRecord[];
  rasters?: { products?: RasterProduct[] } | null;
  onset?: MonsoonOnset | null;
  observations?: { s2_dates?: string[]; landsat_dates?: string[]; s1_dates?: string[] } | null;
}

export function isRampLegend(legend: RasterLegend | null | undefined): legend is RasterRampLegend {
  return Boolean(legend && !Array.isArray(legend) && Array.isArray((legend as RasterRampLegend).ramp));
}

/** Same-origin URL of a raster product file (the route checks ownership). */
export function rasterFileUrl(jobId: string, path: string): string {
  return `/api/monitoring/raster/${encodeURIComponent(jobId)}/${path
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')}`;
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
