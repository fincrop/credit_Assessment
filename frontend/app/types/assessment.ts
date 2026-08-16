/**
 * Assessment / risk-index types for index_v5 + legacy shim.
 */

export type RiskCategory = 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH' | string;

export type ReasonPolarity = 'positive' | 'negative' | 'caveat' | string;

export type TriState = boolean | null;

export interface ReasonCode {
  code?: string;
  message?: string;
  polarity?: ReasonPolarity;
}

export interface RiskSubIndex {
  score: number;
  inputs?: Record<string, unknown>;
  drivers?: Record<string, unknown>;
  gate?: number;
}

export interface RiskBenefits {
  bonus?: number;
  pm_kisan?: TriState;
  has_crop_insurance?: TriState;
}

/**
 * What footprint the score was actually measured over.
 *
 * A consumer must be able to tell a score about the farmer's parcel from a
 * score about the land around it. When `geometry_substituted` is true the
 * polygon on screen is NOT the one we measured, and that has to be said out
 * loud rather than left in a tooltip.
 */
export interface Footprint {
  geometry_source?: string | null;
  geometry_substituted?: boolean;
  note?: string | null;
}

export interface RiskAssessment {
  index_score: number;
  raw_index?: number;
  risk_category: RiskCategory;
  sub_indices?: Record<string, RiskSubIndex | number>;
  weights?: Record<string, number>;
  confidence_gate?: number;
  weak_sub_indices?: string[];
  reason_codes?: ReasonCode[];
  benefits?: RiskBenefits;
  index_version?: string;
  method?: string;
  positioning?: string;
  no_repayment_calibration?: boolean;
  calibration?: Record<string, unknown>;
  footprint?: Footprint;
  /**
   * One grounded sentence per sub-index, built deterministically from the
   * inputs — NOT generated text. Keyed by sub-index name plus
   * `data_confidence` and `footprint`.
   */
  driver_captions?: Record<string, string>;
}

/* ─────────────────────────────────────────────────────────────────────────
   The refusal layer — v6.

   The backend distinguishes three terminal states, and collapsing them loses
   exactly the information a loan officer needs:

     SUCCESS                    we looked; here is the score
     REJECTED_NOT_AGRICULTURAL  we looked; it is not farmland
     INSUFFICIENT_DATA          we could not see it well enough to say

   The last one is a statement about our view of the field, not about the
   field. Rendering it as a low score would be a lie.
   ───────────────────────────────────────────────────────────────────────── */

export type LandCoverClass =
  | 'CROPLAND'
  | 'WATER'
  | 'BUILTUP'
  | 'BARREN'
  | 'FOREST'
  | 'PLANTATION'
  | 'UNKNOWN'
  | string;

/** Gate verdicts: pass = farmland, flag = odd but scored, reject = not scored. */
export type GateOutcome = 'pass' | 'flag' | 'reject' | string;

export interface LandCover {
  outcome?: GateOutcome;
  class?: LandCoverClass;
  confidence?: number;
  reason?: string;
  is_cultivable?: boolean;
  insufficient_data?: boolean;
  gate_version?: string;
  evidence?: {
    streams?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export type ViabilityOutcome = 'viable' | 'marginal' | 'not_viable' | string;

/** Can this parcel be honestly measured at 10 m at all? Runs pre-acquisition. */
export interface ParcelViability {
  version?: string;
  outcome?: ViabilityOutcome;
  reason?: string;
  evidence?: {
    registered_ha?: number;
    geometry_ha?: number;
    effective_ha?: number;
    approx_pixels?: number;
    area_ratio?: number;
    areas_disagree?: boolean;
    thresholds?: {
      min_pixels_hard?: number;
      min_pixels_reliable?: number;
      area_ratio_range?: number[];
    };
  };
}

export type SufficiencyOutcome = 'sufficient' | 'insufficient' | string;

/** Did we observe the parcel often enough to say anything about it? */
export interface DataSufficiency {
  version?: string;
  outcome?: SufficiencyOutcome;
  reason?: string;
  evidence?: {
    n_bins?: number;
    n_observed_bins?: number;
    observed_fraction?: number;
    /** Including radar — the blind gap is measured against this, not optical alone. */
    n_bins_with_any_signal?: number;
    any_signal_fraction?: number;
    n_sar_only_bins?: number;
    largest_blind_gap_days?: number;
    largest_blind_gap_span?: string[];
    n_cycles_detected?: number;
    field_area_ha?: number;
    thresholds?: {
      blind_gap_days?: number;
      min_observed_fraction?: number;
    };
  };
}

export type CropVerificationOutcome =
  | 'consistent'
  | 'inconsistent'
  | 'indeterminate'
  | string;

/**
 * Declared crop vs observed phenology.
 *
 * `inconsistent` is as often a data-entry error as it is misrepresentation.
 * Present it neutrally.
 */
export interface CropVerification {
  version?: string;
  declared_crop?: string | null;
  canonical_crop?: string | null;
  outcome?: CropVerificationOutcome;
  confidence?: number;
  reason?: string;
  evidence?: Record<string, unknown>;
}

export interface SignalQualitySummary {
  valid_fraction?: number;
  sar_fallback_fraction?: number;
  mean_bin_quality?: number;
  n_valid_bins?: number;
  source_counts?: Record<string, number>;
  [key: string]: unknown;
}

export interface CreditAssessment {
  credit_score?: number;
  risk_category?: RiskCategory;
  component_scores?: Record<string, number>;
  component_weights?: Record<string, number>;
  weak_components?: string[];
  method?: string;
  confidence?: number;
  confidence_gate?: number;
  scoring_narrative?: string;
  reason_codes?: ReasonCode[];
  index_version?: string;
  ml_components_silenced?: boolean;
  ml_requested_mode?: string;
  positioning?: string;
}

/** @deprecated Backend no longer emits ₹ recommendations; kept for old jobs only. */
export interface CreditRecommendations {
  recommended_limit?: number;
  recommended_credit_limit?: number;
  limit_per_hectare?: number;
  interest_rate?: number;
  repayment_period_months?: number;
  repayment_months?: number;
  collateral_required?: boolean;
  conditions?: string[];
  reasoning?: string;
  active_cycle_note?: string;
}

export interface AnomalyEvent {
  type?: string;
  impact?: string;
  stage?: string;
  date?: string;
  description?: string;
}

export interface YieldDetail {
  yield_potential_pct?: number | string;
  yield_index_basis?:
    | 'peer_nirv'
    | 'internal_cvi_auc'
    | 'crop_curve_ndvi'
    | 'crop_specific'
    | 'signal_proxy'
    | 'signal_only'
    | 'cycle_proxy'
    | 'signal_based'
    | string;
  peer_n?: number;
  [key: string]: unknown;
}

export interface SeasonPerformance {
  season?: string;
  year?: number;
  crop?: string;
  health_score?: number;
  yield_potential_pct?: number | string;
  yield_potential_score?: number;
  scoring_method?: string;
  is_active_cycle?: boolean;
  performance_narrative?: string;
  anomaly_events?: AnomalyEvent[];
  yield_detail?: YieldDetail;
}

export interface PerformanceAnalysis {
  average_health_score?: number;
  average_yield_score?: number;
  average_performance_score?: number;
  n_complete_cycles?: number;
  n_active_cycles?: number;
  n_seasons_analyzed?: number;
  seasonal_performance?: SeasonPerformance[];
  peer_benchmarking?: { percentile?: number; n?: number; activated?: boolean };
}

export interface WeatherIndicators {
  available?: boolean;
  spi_like?: number;
  spei_like?: number;
  /** Backend key from weather_analyzer */
  max_dry_spell_days?: number;
  max_wet_spell_days?: number;
  gdd_total?: number;
  monsoon_onset_offset_days?: number;
  /** Legacy / alternate aliases (older docs) */
  dry_spell_max_days?: number;
  wet_spell_max_days?: number;
  gdd?: number;
  monsoon_onset_anomaly_days?: number;
  heat_stress_days?: number;
  cold_stress_days?: number;
  [key: string]: unknown;
}

export interface WeatherAnalysis {
  weather_risk_score?: number;
  total_extreme_events?: number;
  critical_stage_events?: number;
  cycle_risk_scores?: { risk_score?: number; n_events?: number; cycle_id?: string }[];
  extreme_events?: Record<string, unknown>[];
  kharif_avg_rainfall_mm?: number;
  rabi_avg_rainfall_mm?: number;
  seasonal_weather?: {
    season?: string;
    year?: number;
    weather_indicators?: WeatherIndicators;
    [key: string]: unknown;
  }[];
  forward_exposure?: Record<string, unknown>;
  backward_resilience?: Record<string, unknown>;
  weather_sources_used?: string[];
  weather_indicators_present?: boolean;
}

export interface WeatherInterval {
  cycle_id?: string;
  crop?: string;
  start_date?: string;
  end_date?: string;
  duration_days?: number;
  weather_risk?: number;
  event_count?: number;
  events?: Record<string, unknown>[];
}

export interface CroppingAnalysis {
  seasons_with_crops?: number;
  total_seasons_analyzed?: number;
  cropping_intensity?: number;
  dominant_crop?: string;
  cultivation_signal?: number;
  crops_detected?: Record<string, unknown> | string[];
  region?: string;
  season_results?: Record<string, unknown>[];
  crop_verification?: CropVerification;
}

export interface ContinuousDataStats {
  grid_slots?: number;
  valid_observations?: number;
  missing_observations?: number;
  interval_days?: number;
  date_range?: { start?: string; end?: string };
  total_days?: number;
}

export interface CyclePhenology {
  fit_ok?: boolean;
  r2?: number;
  sos?: string;
  pos?: string;
  eos?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface CropCycle {
  season_label?: string;
  season_type?: string;
  sowing_date?: string;
  harvest_date?: string;
  duration_days?: number;
  phenology?: CyclePhenology;
  [key: string]: unknown;
}

export interface CropCyclesBlock {
  detected?: boolean;
  cycles_count?: number;
  method?: string;
  utilization_metrics?: {
    land_utilization_index?: number;
    crops_per_year?: number;
    cropping_pattern?: string;
  };
  cycles?: CropCycle[];
}

export interface AiExplainability {
  method?: string;
  shap_available?: boolean;
  credit_summary?: string;
  top_positive_drivers?: {
    feature?: string;
    label?: string;
    contribution?: number;
    value?: number;
  }[];
  top_negative_drivers?: {
    feature?: string;
    label?: string;
    contribution?: number;
    value?: number;
  }[];
}

export interface RoadmapStep {
  step?: number;
  action?: string;
  timeframe?: string;
  score_gain?: number;
  feasibility?: string;
}

export interface AiCounterfactuals {
  current_score?: number;
  current_risk_category?: string;
  projected_score_all_improvements?: number;
  scenarios?: {
    id?: string;
    title?: string;
    score_gain?: number;
    component?: string;
    feasibility?: string;
    action?: string;
  }[];
  /** Backend emits list of steps; slim path may stringify */
  improvement_roadmap?: RoadmapStep[] | string;
}

export interface AiEnrichment {
  groq_used?: boolean;
  groq_skipped_reason?: string;
  narrative_source?: 'groq' | 'deterministic' | 'minimal' | string;
  english_narrative?: string;
  translated_narrative?: string;
  translation_language?: string;
  explainability?: AiExplainability;
  explainability_mongo?: AiExplainability;
  counterfactuals?: AiCounterfactuals | Record<string, unknown>;
  counterfactuals_mongo?: AiCounterfactuals;
}

export interface AssessmentPayload {
  farmer_id?: string;
  crop_hint?: string;
  sowing_date?: string;
  status?: string;
  error?: string;
  traceback?: string;
  assessment_date?: string;
  pipeline_version?: string;
  pipeline_profile?: string;
  pipeline_stages?: string[];
  processing_time_seconds?: number;
  ml_mode?: string;
  location?: {
    latitude?: number;
    longitude?: number;
    region?: string;
  };
  field_area_ha?: number;

  /* ── The refusal layer. Stamped on every assessment, including passes,
        so a lender sees the evidence and not just the answer. ── */
  land_cover?: LandCover;
  parcel_viability?: ParcelViability;
  data_sufficiency?: DataSufficiency;
  /** Set alongside status INSUFFICIENT_DATA. */
  insufficient_reason?: string;
  /** Set alongside status REJECTED_NOT_AGRICULTURAL. */
  rejection_reason?: string;
  rejection_class?: LandCoverClass;

  continuous_data_stats?: ContinuousDataStats;
  satellite_data?: Record<string, unknown>;
  risk_assessment?: RiskAssessment;
  /** Multi-farm aggregate (when assessment_type === multi_farm). */
  farmer_level?: FarmerLevel;
  farm_assessments?: FarmAssessment[];
  assessment_type?: string;
  method?: string;
  n_plots_total?: number;
  n_plots_scored?: number;
  n_plots_failed?: number;
  n_plots_skipped?: number;
  signal_quality_summary?: SignalQualitySummary;
  index_version?: string;
  credit_assessment?: CreditAssessment;
  /** @deprecated index_v5 no longer emits ₹ recommendations */
  credit_recommendations?: CreditRecommendations;
  cropping_analysis?: CroppingAnalysis;
  performance_analysis?: PerformanceAnalysis;
  weather_analysis?: WeatherAnalysis;
  weather_intervals?: WeatherInterval[];
  crop_cycles?: CropCyclesBlock;
  farmer_benefits?: {
    pm_kisan_enrolled?: TriState;
    has_crop_insurance?: TriState;
  };
  ai_enrichment?: AiEnrichment;
  warnings?: string[];
  errors?: string[];
  summary?: Record<string, unknown>;
}

/** Farmer-level aggregate over all owned plots (multi_farm_aggregate_v5). */
export interface Diversification {
  score: number;
  bonus: number;
  n_crops: number;
  n_districts: number;
  n_seasons?: number;
  n_plots: number;
  note?: string;
}

export interface FarmerLevelBenefits {
  bonus?: number;
  conferred?: string[];
  pm_kisan?: TriState;
  has_crop_insurance?: TriState;
  note?: string;
}

export interface FarmerLevel {
  index_score: number | null;
  raw_index?: number;
  risk_category: RiskCategory;
  confidence_gate: number | null;
  sub_indices: Record<string, number>;
  weights: Record<string, number>;
  weak_sub_indices?: string[];
  diversification?: Diversification;
  benefits?: FarmerLevelBenefits;
  portfolio_bonus?: number;
  reason_codes?: ReasonCode[];
  n_plots_total?: number;
  n_plots_scored?: number;
  n_plots_owned?: number;
  total_scored_area_ha?: number;
  weather_shared?: boolean;
}

export interface FarmAssessment {
  plot_key?: string;
  farm_id?: string;
  area_ha: number;
  tenure_factor: number;
  included: boolean;
  is_ror_owner?: boolean | null;
  crop?: string | null;
  district?: string | null;
  season_types?: string[];
  index_score?: number | null;
  raw_index?: number;
  risk_category?: string;
  confidence_gate?: number | null;
  sub_indices: Record<string, number>;
  reason_codes?: ReasonCode[];
  /**
   * Prefixed by kind, and the prefix carries meaning: only `error:` is a
   * failure. `not_agricultural:` and `insufficient_observation` are legitimate
   * exclusions and must not make a holding look broken.
   */
  skipped_reason?: string;
  /** Slim land-cover verdict on a plot excluded as non-agricultural. */
  land_cover?: Pick<LandCover, 'class' | 'confidence' | 'reason'>;
  /** Slim sufficiency verdict on a plot we could not observe. */
  data_sufficiency?: {
    reason?: string;
    observed_fraction?: number;
    largest_blind_gap_days?: number;
  };
  /** Compact plot analysis for farm detail tabs (multi-farm slim payload). */
  detail?: {
    cropping_analysis?: CroppingAnalysis;
    performance_analysis?: PerformanceAnalysis;
    weather_analysis?: WeatherAnalysis;
    weather_intervals?: WeatherInterval[];
    crop_cycles?: CropCyclesBlock;
    continuous_data_stats?: ContinuousDataStats;
    location?: AssessmentPayload['location'];
    ai_enrichment?: AiEnrichment;
  };
}

/** Mid-run progress — partial_result never includes farmer_level. */
export interface JobPartialResult {
  farmer_id?: string;
  farm_assessments: FarmAssessment[];
  n_plots_total?: number;
  n_plots_scored?: number;
  n_plots_skipped?: number;
  n_plots_failed?: number;
  n_plots_done?: number;
}

export interface JobProgress {
  current_stage?: string;
  pipeline_stages?: string[];
  n_plots_total?: number;
  n_plots_done?: number;
  n_plots_scored?: number;
  n_plots_skipped?: number;
  n_plots_failed?: number;
  pending_plot_keys?: string[];
  /** Farm rows only — never farmer_level / index_score aggregate. */
  partial_result?: JobPartialResult;
}

export interface AssessmentJob {
  job_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED';
  farmer_id?: string;
  result?: AssessmentPayload;
  error?: string;
  progress?: JobProgress | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  completed_at?: string;
}
