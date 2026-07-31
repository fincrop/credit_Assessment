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
  continuous_data_stats?: ContinuousDataStats;
  satellite_data?: Record<string, unknown>;
  risk_assessment?: RiskAssessment;
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

export interface AssessmentJob {
  job_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED';
  farmer_id?: string;
  result?: AssessmentPayload;
  error?: string;
  created_at?: string;
  updated_at?: string;
}
