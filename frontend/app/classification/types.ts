/**
 * Shared types for the Crop Classification journey.
 *
 * The unit of work here is an **area of interest** (a village, a block, a
 * drawn region) rather than a farm. That is the whole difference from the
 * Farmer Journey: there, the user already knows the parcel and wants a score;
 * here, the parcel boundaries do not exist yet and the pipeline has to find
 * them before it can name a crop.
 */

export type AoiSource = 'drawn' | 'uploaded';

export interface AreaOfInterest {
  aoi_id: string;
  /** Display name — file name, or "Drawn area 1". */
  name: string;
  source: AoiSource;
  boundary: GeoJSON.Polygon | GeoJSON.MultiPolygon;
  area_ha: number;
  centroid: { lat: number; lng: number };
  color?: string;
}

/** Seasons the classifier knows. Values match the cycle detector's own labels. */
export const SEASONS = [
  { value: 'kharif', label: 'Kharif', hint: 'Jun–Oct · monsoon sown' },
  { value: 'rabi', label: 'Rabi', hint: 'Oct–Mar · winter sown' },
  { value: 'zaid', label: 'Zaid', hint: 'Mar–Jun · summer' },
  { value: 'whole_year', label: 'Whole year', hint: 'All cycles detected' },
] as const;

export type SeasonValue = (typeof SEASONS)[number]['value'];

/**
 * The 18 crops the model was trained on. Anything outside this list is not a
 * class the model can emit -- it abstains instead (see `region_guard`), which
 * is deliberate: a learned catch-all with no examples becomes an attractor.
 */
export const CLASSIFIABLE_CROPS = [
  'Bajra', 'Banana', 'Chilli', 'Cotton', 'Gram', 'Grapes', 'Groundnut',
  'Jowar', 'Maize', 'Mustard', 'Onion', 'Potato', 'Rice', 'Soyabean',
  'Sugarcane', 'Tobacco', 'Tur', 'Wheat',
] as const;

/**
 * Stable colour per crop for the result map and legend.
 *
 * Hue carries crop identity, so the assignment is fixed rather than
 * index-based: the same crop must be the same colour across two runs, or two
 * maps cannot be compared. Grouped by crop family so a misread within a family
 * is visually a near-miss rather than a jump across the palette.
 */
export const CROP_COLORS: Record<string, string> = {
  // cereals — warm golds
  Rice: '#C9A227', Wheat: '#E0B94A', Maize: '#F0CE6D',
  Bajra: '#A8862B', Jowar: '#8C6F22',
  // pulses — earthy reds
  Gram: '#B4553A', Tur: '#8E3F2C',
  // oilseeds — oranges
  Groundnut: '#D97A34', Mustard: '#E8A03C', Soyabean: '#B5652A',
  // fibre / commercial — purples
  Cotton: '#7C5FA8', Tobacco: '#5D477E',
  // perennial — deep greens
  Sugarcane: '#2E7D4F', Banana: '#3F9E68', Grapes: '#276145',
  // horticulture — blues/teals
  Onion: '#3E7FA8', Potato: '#5FA3C4', Chilli: '#2A5F80',
  // intercrop — a close soybean/tur split inside one 10 m parcel
  'Soyabean+Tur': '#A2512B',
};

/** Intercrop classes: named, but not one of the 18 single-crop classes. */
export const INTERCROP_CLASSES = ['Soyabean+Tur'] as const;

/** Non-crop outcomes the map must be able to draw. */
export const NON_CROP_COLORS: Record<string, string> = {
  'Non-agricultural': '#9A9287',
  Water: '#4A7FA5',
  Fallow: '#C4B99F',
  Unclassified: '#B0A89C',
  Abstained: '#8F8779',
  /** Older runs. New runs use Others. */
  'Not requested': '#D6CFC2',
  /** Model named a crop that is not printed as that crop. The name is `model_top_crop`. */
  Others: '#C4BBAE',
  /** Too few clear looks to say anything. Never shown as Fallow. */
  'Insufficient data': '#E4DED3',
};

export function classColor(name: string): string {
  return CROP_COLORS[name] || NON_CROP_COLORS[name] || '#B0A89C';
}

/** Per-field decision the classifier attaches to every polygon. */
export type FieldStatus =
  | 'confirmed'
  | 'provisional'
  | 'intercrop'
  | 'abstained'
  | 'not_requested'
  | 'no_cycle'
  | 'no_data';

export const FIELD_STATUS_LABELS: Record<FieldStatus, string> = {
  confirmed: 'Confirmed',
  provisional: 'Provisional',
  intercrop: 'Intercrop',
  abstained: 'Abstained',
  not_requested: 'Other crop',
  no_cycle: 'No crop cycle',
  no_data: 'Insufficient data',
};

/** Properties of one classified field polygon. Every key is optional so older results still read. */
export interface ClassifiedFieldProps {
  field_id?: string | number;
  crop?: string;
  area_ha?: number;
  confidence?: number;
  color?: string;
  note?: string;
  status?: FieldStatus;
  /** The model's own top crop — differs from `crop` when it was not requested. */
  model_top_crop?: string | null;
  top2_crop?: string | null;
  p_top1?: number | null;
  p_top2?: number | null;
  margin?: number | null;
  cycle_complete?: boolean | null;
  n_obs_cycle?: number | null;
  abstain_reason?: string | null;
  region_support?: string | boolean | null;
  season_consistent?: boolean | null;
  ecoregion?: string | null;
  cycle_sowing?: string | null;
  cycle_peak?: string | null;
  cycle_harvest?: string | null;
  /** Uncertain field whose best guess is a requested crop (still shown as Others). */
  possible_crop?: string | null;
  /** Fused classifier evidence: clear optical looks, radar looks, share of the curve imputed from radar. */
  n_obs_optical?: number | null;
  n_obs_radar?: number | null;
  frac_imputed?: number | null;
}

export interface ClassificationInputs {
  /** Display name for this run — history, result header, and download filenames. */
  region_name: string;
  /** SBI revenue circle this village belongs to. Empty when the run is not part of a cluster. */
  rc_id: string;
  season: SeasonValue;
  /** Agricultural year the season belongs to, e.g. 2024 for Rabi 2024/25. */
  year: number;
  /** Restrict the model to this subset. Empty = all 18. */
  target_crops: string[];
  /** Minimum field size to keep after vectorization, hectares. */
  min_field_area_ha: number;
  /**
   * Probability below which a segment is labelled `Abstained` rather than
   * given a crop name. Defaults to the measured 0.25 gate.
   */
  confidence_threshold: number;
  /** Apply the region-support guard (abstain where the crop has no local training data). */
  apply_region_guard: boolean;
  /** Apply the season posterior mask derived from the cycle's own dates. */
  apply_season_mask: boolean;
  /** Field-boundary source. 'auto' = ALU if configured, else watershed, else FTW. */
  delineation_method: DelineationMethod;
}

export type DelineationMethod = 'auto' | 'alu' | 'hybrid' | 'ftw' | 'watershed' | 'snic';

export const DELINEATION_METHODS: { value: DelineationMethod; label: string; hint: string }[] = [
  { value: 'auto', label: 'Best available', hint: 'Google ALU when enabled, otherwise the watershed segmenter (best measured at 10 m).' },
  { value: 'alu', label: 'Google ALU (sub-metre)', hint: 'Needs the Agricultural Understanding API key on the server.' },
  { value: 'hybrid', label: 'FTW + watershed fusion', hint: 'Fields of The World polygons, gaps filled by our watershed segmenter.' },
  { value: 'ftw', label: 'Fields of The World 2024/25', hint: 'Open global field polygons (10 m model, CC-BY-4.0).' },
  { value: 'watershed', label: 'Multi-temporal watershed', hint: 'Our own: year-round Sentinel-2 edge map + seeded watershed.' },
  { value: 'snic', label: 'Legacy superpixels (SNIC)', hint: 'Previous segmenter; kept for comparison.' },
];

export const DEFAULT_INPUTS: ClassificationInputs = {
  region_name: '',
  rc_id: '',
  season: 'kharif',
  year: new Date().getFullYear(),
  target_crops: [],
  min_field_area_ha: 0.2,
  confidence_threshold: 0.25,
  apply_region_guard: true,
  apply_season_mask: true,
  delineation_method: 'auto',
};

/** Pre-flight checks run on the AOI before any satellite work is paid for. */
export interface ValidationCheck {
  id: string;
  label: string;
  status: 'pending' | 'running' | 'pass' | 'warn' | 'fail';
  detail?: string;
  /** e.g. 0.82 for "82% of the AOI is cropland". */
  value?: number;
}

export type JobStage =
  | 'queued'
  | 'validating'
  | 'extracting'
  | 'segmenting'
  | 'classifying'
  | 'vectorizing'
  | 'complete'
  | 'failed';

export const STAGE_LABELS: Record<JobStage, string> = {
  queued: 'Queued',
  validating: 'Validating area',
  extracting: 'Pulling satellite imagery',
  segmenting: 'Finding field boundaries',
  classifying: 'Classifying crops',
  vectorizing: 'Building vector layer',
  complete: 'Complete',
  failed: 'Failed',
};

export interface JobProgress {
  job_id: string;
  stage: JobStage;
  /** 0–100 within the current stage; null when the stage cannot report it. */
  percent: number | null;
  message?: string;
  checks: ValidationCheck[];
  started_at?: string;
  finished_at?: string;
  error?: string;
}

export interface ClassStat {
  crop: string;
  field_count: number;
  area_ha: number;
  area_share: number;
  /** Mean model confidence across fields of this class. */
  mean_confidence: number;
}

export interface ClassificationResult {
  job_id: string;
  aoi_name: string;
  season: SeasonValue;
  year: number;
  total_area_ha: number;
  classified_area_ha: number;
  /** Area the model declined to name — abstentions plus non-agricultural. */
  unclassified_area_ha: number;
  field_count: number;
  mean_confidence: number;
  stats: ClassStat[];
  /** Vectorized fields, ready to draw. Kept as a FeatureCollection so the map
   *  layer and the GeoJSON download are the same object. */
  fields: GeoJSON.FeatureCollection;
  /** Scene dates actually used, for the provenance note under the map. */
  scenes_used?: string[];
  /** Model file name (older results: the feature-extractor version). */
  model_version?: string;
  model?: {
    name?: string;
    path?: string;
    sha256?: string;
    extractor_version?: string;
    classes?: string[];
  };
  /** Observation window the classifier read; `end`/`as_of` is the last date seen. */
  window?: { start?: string; end?: string; as_of?: string };
  target_crops?: string[];
  validation_checks?: ValidationCheck[];
  /** Fields printed as Others whose best (uncertain) guess is a requested crop, by crop. */
  possible_requested?: Record<string, { field_count: number; area_ha: number }>;
  /**
   * Cross-region caveat. The classifier scores ~0.81 balanced accuracy inside
   * regions it has training data for and ~0.22 outside them, so a result in an
   * unsupported region is reported but flagged rather than presented as equal.
   */
  region_support?: {
    ecoregion: string;
    supported: boolean;
    unsupported_crops: string[];
  };
}

export type DownloadFormat = 'geojson' | 'shapefile' | 'geotiff' | 'csv' | 'png';

export interface DownloadOption {
  format: DownloadFormat;
  label: string;
  hint: string;
}

export const DOWNLOAD_OPTIONS: DownloadOption[] = [
  { format: 'geojson', label: 'GeoJSON', hint: 'Field polygons with crop labels' },
  { format: 'shapefile', label: 'Shapefile (.zip)', hint: 'For QGIS / ArcGIS' },
  { format: 'geotiff', label: 'GeoTIFF', hint: 'Classified raster' },
  { format: 'csv', label: 'CSV', hint: 'Per-field table and statistics' },
  { format: 'png', label: 'Map image (PNG)', hint: 'Rendered map with legend' },
];

/** Ring area in hectares via the spherical excess formula. */
export function ringAreaHa(ring: number[][]): number {
  if (ring.length < 4) return 0;
  const R = 6378137;
  let total = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[i + 1];
    total +=
      ((lon2 - lon1) * Math.PI) / 180 *
      (2 + Math.sin((lat1 * Math.PI) / 180) + Math.sin((lat2 * Math.PI) / 180));
  }
  return Math.abs((total * R * R) / 2) / 10000;
}

export function geometryAreaHa(geom: GeoJSON.Polygon | GeoJSON.MultiPolygon): number {
  if (geom.type === 'Polygon') {
    return geom.coordinates.reduce(
      (sum, ring, i) => sum + (i === 0 ? ringAreaHa(ring) : -ringAreaHa(ring)),
      0
    );
  }
  return geom.coordinates.reduce(
    (sum, poly) =>
      sum +
      poly.reduce((s, ring, i) => s + (i === 0 ? ringAreaHa(ring) : -ringAreaHa(ring)), 0),
    0
  );
}

export function geometryCentroid(
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon
): { lat: number; lng: number } {
  const rings =
    geom.type === 'Polygon' ? [geom.coordinates[0]] : geom.coordinates.map((p) => p[0]);
  let lat = 0;
  let lng = 0;
  let n = 0;
  for (const ring of rings) {
    for (let i = 0; i < ring.length - 1; i++) {
      lng += ring[i][0];
      lat += ring[i][1];
      n += 1;
    }
  }
  return n ? { lat: lat / n, lng: lng / n } : { lat: 0, lng: 0 };
}

export function formatHa(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k ha`;
  if (v >= 100) return `${v.toFixed(0)} ha`;
  return `${v.toFixed(1)} ha`;
}

/** Summary of a past job. The classified field polygons are not included —
 *  reopen goes through `/api/classification/result/:id` for those. */
export interface ClassificationHistoryItem {
  job_id: string;
  stage: JobStage;
  created_at?: string;
  finished_at?: string;
  error?: string;
  aoi_name: string;
  season: SeasonValue;
  year: number;
  total_area_ha: number;
  field_count?: number;
  classified_area_ha?: number;
  mean_confidence?: number;
  stats: ClassStat[];
  areas: AreaOfInterest[];
  inputs: ClassificationInputs;
}

export function isJobStage(v: unknown): v is JobStage {
  return typeof v === 'string' && v in STAGE_LABELS;
}

export function inputsFromStored(raw: unknown): ClassificationInputs {
  if (!raw || typeof raw !== 'object') return { ...DEFAULT_INPUTS };
  const r = raw as Record<string, unknown>;
  const season = SEASONS.some((s) => s.value === r.season)
    ? (r.season as SeasonValue)
    : DEFAULT_INPUTS.season;
  return {
    region_name:
      typeof r.region_name === 'string' ? r.region_name : DEFAULT_INPUTS.region_name,
    rc_id: typeof r.rc_id === 'string' ? r.rc_id : '',
    season,
    year: Number.isFinite(Number(r.year)) ? Number(r.year) : DEFAULT_INPUTS.year,
    target_crops: Array.isArray(r.target_crops)
      ? r.target_crops.filter((c): c is string => typeof c === 'string')
      : [],
    min_field_area_ha: Number.isFinite(Number(r.min_field_area_ha))
      ? Number(r.min_field_area_ha)
      : DEFAULT_INPUTS.min_field_area_ha,
    confidence_threshold: Number.isFinite(Number(r.confidence_threshold))
      ? Number(r.confidence_threshold)
      : DEFAULT_INPUTS.confidence_threshold,
    apply_region_guard: r.apply_region_guard !== false,
    apply_season_mask: r.apply_season_mask !== false,
    delineation_method: DELINEATION_METHODS.some((m) => m.value === r.delineation_method)
      ? (r.delineation_method as DelineationMethod)
      : DEFAULT_INPUTS.delineation_method,
  };
}

export function areasFromStored(raw: unknown): AreaOfInterest[] {
  if (!Array.isArray(raw)) return [];
  const out: AreaOfInterest[] = [];
  raw.forEach((item, i) => {
    if (!item || typeof item !== 'object') return;
    const a = item as Record<string, unknown>;
    const boundary = a.boundary as GeoJSON.Polygon | GeoJSON.MultiPolygon | undefined;
    if (!boundary || (boundary.type !== 'Polygon' && boundary.type !== 'MultiPolygon')) return;
    const centroid =
      a.centroid && typeof a.centroid === 'object'
        ? (a.centroid as { lat?: number; lng?: number })
        : geometryCentroid(boundary);
    out.push({
      aoi_id: typeof a.aoi_id === 'string' && a.aoi_id ? a.aoi_id : `hist-${i + 1}`,
      name: typeof a.name === 'string' && a.name ? a.name : `Area ${i + 1}`,
      source: a.source === 'uploaded' ? 'uploaded' : 'drawn',
      boundary,
      area_ha: Number.isFinite(Number(a.area_ha)) ? Number(a.area_ha) : geometryAreaHa(boundary),
      centroid: { lat: Number(centroid.lat) || 0, lng: Number(centroid.lng) || 0 },
      color: typeof a.color === 'string' ? a.color : undefined,
    });
  });
  return out;
}

export function seasonLabel(season: string): string {
  return SEASONS.find((s) => s.value === season)?.label || season.replace(/_/g, ' ');
}
