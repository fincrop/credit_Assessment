/**
 * The report contract — `report_payload_v1`.
 *
 * Mirrors `backend/Credit_assessment/api/report_payload.py` field for field.
 * This is a DIFFERENT contract from `AssessmentPayload`: the report is a
 * curated, provenance-carrying view assembled from the assessment, its
 * evidence record and the score history. Rendering it through the dashboard's
 * types would guarantee drift between the two.
 *
 * WHAT THIS CONTRACT DELIBERATELY OMITS
 * ─────────────────────────────────────
 * The backend refuses to emit several panels the supplied design has, and
 * says so in `omitted{}` rather than leaving them silently absent. A renderer
 * must be able to tell "no data yet" from "we do not do this":
 *
 *   policy_action    — no policy engine exists; a recommendation, committee
 *                      threshold or re-assessment date would be invented
 *   peer_comparison  — needs a warm cohort of assessed parcels in this zone
 *   review_workflow  — no reviewer workflow exists
 *
 * Farmer identity is NOT in the payload. The endpoint never reaches into
 * farm_info for Aadhaar or mobile, because the masking policy is unsettled
 * (backend D-5). `farmer` and `prepared_by` are supplied by the caller from
 * whatever it is already authorised to display.
 *
 * SCALE
 * ─────
 * KBS is 300–900 over four agronomic bands — the same scale as the dashboard
 * gauge. The design's 300–950 / five credit-policy bands were NOT adopted:
 * policy bands imply a policy engine.
 */

import type {
  CropVerification,
  DataSufficiency,
  Footprint,
  LandCover,
  ParcelViability,
  ReasonCode,
  RiskCategory,
} from './assessment';

export const REPORT_PAYLOAD_VERSION = 'report_payload_v1';

export interface KbsBandRange {
  name: string;
  min: number;
  max: number;
}

export interface ReportScore {
  /** Null when no score was produced. Render a refusal, never a placeholder. */
  kbs: number | null;
  scale_min: number;
  scale_max: number;
  band: KbsBandRange | null;
  bands: KbsBandRange[];
  index_score: number | null;
  raw_index: number | null;
  risk_category: RiskCategory | null;
  confidence_gate: number | null;
  index_version?: string | null;
  /**
   * Stated in the payload so a renderer cannot accidentally present the score
   * as a loan amount. It is an agronomic risk index and nothing else.
   */
  positioning: 'agronomic_risk_index' | string;
  no_repayment_calibration: boolean;
}

/**
 * Movement against the previous assessment.
 *
 * `null` on a first assessment — NOT a delta of zero. A "0" against a baseline
 * that does not exist invents a history. When this is null there is no trend
 * chip at all, not a chip reading "—".
 */
export interface ReportTrend {
  previous_kbs: number;
  previous_date?: string | null;
  delta: number;
  direction: 'up' | 'down' | 'flat';
}

export interface ReportSubIndex {
  key: string;
  score: number | null;
  weight: number | null;
  is_weakest: boolean;
  /** Deterministic, grounded in the sub-index inputs. Not generated prose. */
  caption?: string | null;
}

export interface ReportDataConfidence {
  score: number | null;
  gate: number | null;
  caption?: string | null;
}

/**
 * The parcel's own NDVI trajectory, with provenance.
 *
 * `comparison_available` is always false until a peer cohort is warm, and
 * `comparison_note` carries the reason. A synthesised district median would be
 * a fabrication in the most visually persuasive part of the report.
 */
/** Per-bin provenance. `optical`/`fused` are observed; the rest are not. */
export type SignalSource = 'optical' | 'fused' | 'sar' | 'imputed' | string;

export interface ReportNdviTrajectory {
  dates: string[];
  /** `null` means no observation in that bin. Never interpolate across one. */
  ndvi: (number | null)[];
  /**
   * PER-BIN, parallel to `dates` — not a single label for the series.
   * `evidence_snapshot.py` stores the whole `signal_source` column, so a
   * consumer never has to guess whether a given value was measured or
   * reconstructed. Rendering this as one series-level string would throw away
   * exactly the information that makes the chart honest.
   */
  signal_source?: (SignalSource | null)[] | null;
  /** Points actually observed, vs total grid slots. The gap is the story. */
  n_present?: number | null;
  n_total?: number | null;
  comparison_available: boolean;
  comparison_note?: string | null;
}

export interface ReportNarrative {
  text?: string | null;
  /** `groq` | `deterministic` | `minimal` — provenance of the prose. */
  source?: string | null;
  translated?: string | null;
  translation_language?: string | null;
  model?: unknown;
}

export interface ReportMethodology {
  pipeline_version?: string | null;
  index_version?: string | null;
  signal_version?: string | null;
  satellite_provider?: string | null;
  cloud_mask_version?: string | null;
  window?: unknown;
  weights?: Record<string, number> | null;
  weights_note?: string | null;
}

export interface ReportParcel {
  plot_key?: string | null;
  farm_id?: string | null;
  tenure?: string | null;
  area_ha?: number | null;
  crop?: string | null;
  kbs?: number | null;
  band?: KbsBandRange | null;
  included?: boolean;
  skipped_reason?: string | null;
}

export interface ReportHolding {
  n_plots_total?: number | null;
  n_plots_scored?: number | null;
  total_area_ha?: number | null;
  owned_area_ha?: number | null;
  leased_area_ha?: number | null;
  n_owned?: number | null;
  n_leased?: number | null;
  centroid?: { latitude?: number | null; longitude?: number | null } | null;
}

export interface ReportWeatherSnapshot {
  weather_risk_score?: number | null;
  total_extreme_events?: number | null;
  kharif_avg_rainfall_mm?: number | null;
  rabi_avg_rainfall_mm?: number | null;
  max_dry_spell_days?: number | null;
  max_heat_stress_days?: number | null;
}

export interface ReportObservations {
  n_present?: number | null;
  n_total?: number | null;
  observed_fraction?: number | null;
  window_start?: string | null;
  window_end?: string | null;
  satellite_provider?: string | null;
}

export interface ReportBenefits {
  pm_kisan?: boolean | null;
  has_crop_insurance?: boolean | null;
}

export interface ReportIntegrity {
  /** Hash over report content, EXCLUDING generated_at — so the same
   *  assessment always hashes the same. A hash that changes on every render
   *  proves nothing. */
  content_sha256: string;
  note?: string;
}

/** Panels this pipeline will not fill, keyed by panel, valued by the reason. */
export type ReportOmitted = Record<string, string>;

/**
 * Which sections have content.
 *
 * Distinct from `omitted`: this says "we have no data for this yet", while
 * `omitted` says "this pipeline does not produce that". A renderer needs both.
 */
export interface ReportSectionsPresent {
  score: boolean;
  trend: boolean;
  sub_indices: boolean;
  ndvi_trajectory: boolean;
  land_cover: boolean;
  crop_verification: boolean;
  narrative: boolean;
  holding?: boolean;
  weather_snapshot?: boolean;
}

export interface ReportPayload {
  payload_version: string;
  generated_at?: string;
  farmer_id?: string | null;
  assessment_date?: string | null;
  /** `KBS-<yyyymmdd>-<hash8>` — stable for a given assessment. */
  report_id: string;
  status: string;

  /** Caller-supplied. Empty unless the edge passed identity in. */
  farmer: Record<string, unknown>;
  prepared_by: Record<string, unknown>;

  score: ReportScore;
  trend: ReportTrend | null;
  sub_indices: ReportSubIndex[];
  data_confidence: ReportDataConfidence;
  reason_codes: ReasonCode[];

  ndvi_trajectory: ReportNdviTrajectory | null;
  land_cover: LandCover | null;
  parcel_viability: ParcelViability | null;
  data_sufficiency: DataSufficiency | null;
  crop_verification: CropVerification | null;
  footprint: Footprint | null;

  parcels?: ReportParcel[];
  holding?: ReportHolding;
  weather_snapshot?: ReportWeatherSnapshot | null;
  observations?: ReportObservations;
  benefits?: ReportBenefits;

  narrative: ReportNarrative;
  methodology: ReportMethodology;
  omitted: ReportOmitted;
  integrity: ReportIntegrity;

  /** Added by the endpoint, not by the payload builder. */
  sections_present?: ReportSectionsPresent;
}

export interface ReportResponse {
  success: boolean;
  report: ReportPayload;
}
