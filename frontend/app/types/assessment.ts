/**
 * Loose types for pipeline / API JSON.
 * Handles both slim API responses and full payloads.
 * Migrated from frontend/src/types/assessment.ts
 */

export type RiskCategory = 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH' | string;

export interface CreditAssessment {
  credit_score?: number;
  risk_category?: RiskCategory;
  component_scores?: Record<string, number>;
  component_weights?: Record<string, number>;
  weak_components?: string[];
  method?: string;
  confidence?: number;
  scoring_narrative?: string;
  ml_components_silenced?: boolean;
  ml_requested_mode?: string;
}

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

export interface SeasonPerformance {
  season?: string;
  year?: number;
  crop?: string;
  health_score?: number;
  yield_potential_pct?: number | string;
  scoring_method?: string;
  is_active_cycle?: boolean;
  performance_narrative?: string;
  anomaly_events?: AnomalyEvent[];
}

export interface PerformanceAnalysis {
  average_health_score?: number;
  average_yield_score?: number;
  average_performance_score?: number;
  n_complete_cycles?: number;
  n_active_cycles?: number;
  n_seasons_analyzed?: number;
  seasonal_performance?: SeasonPerformance[];
}

export interface WeatherAnalysis {
  weather_risk_score?: number;
  total_extreme_events?: number;
  critical_stage_events?: number;
  cycle_risk_scores?: { risk_score?: number; n_events?: number; cycle_id?: string }[];
  extreme_events?: Record<string, unknown>[];
  kharif_avg_rainfall_mm?: number;
  rabi_avg_rainfall_mm?: number;
  seasonal_weather?: Record<string, unknown>[];
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

export interface CropCyclesBlock {
  detected?: boolean;
  cycles_count?: number;
  method?: string;
  utilization_metrics?: {
    land_utilization_index?: number;
    crops_per_year?: number;
    cropping_pattern?: string;
  };
  cycles?: Record<string, unknown>[];
}

export interface AiExplainability {
  method?: string;
  shap_available?: boolean;
  credit_summary?: string;
  top_positive_drivers?: { feature?: string; label?: string; contribution?: number; value?: number }[];
  top_negative_drivers?: { feature?: string; label?: string; contribution?: number; value?: number }[];
}

export interface AiCounterfactuals {
  current_score?: number;
  projected_score_all_improvements?: number;
  scenarios?: {
    id?: string;
    title?: string;
    score_gain?: number;
    component?: string;
    feasibility?: string;
  }[];
  improvement_roadmap?: string;
}

export interface AiEnrichment {
  groq_used?: boolean;
  groq_skipped_reason?: string;
  english_narrative?: string;
  translated_narrative?: string;
  english_preview?: string;
  translated_preview?: string;
  translation_language?: string;
  explainability?: AiExplainability;
  explainability_mongo?: AiExplainability;
  counterfactuals?: Record<string, unknown>;
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
  credit_assessment?: CreditAssessment;
  credit_recommendations?: CreditRecommendations;
  cropping_analysis?: CroppingAnalysis;
  performance_analysis?: PerformanceAnalysis;
  weather_analysis?: WeatherAnalysis;
  weather_intervals?: WeatherInterval[];
  crop_cycles?: CropCyclesBlock;
  farmer_benefits?: {
    pm_kisan_enrolled?: boolean;
    has_crop_insurance?: boolean;
  };
  ai_enrichment?: AiEnrichment;
  warnings?: string[];
  errors?: string[];
  summary?: Record<string, unknown>;
}

/** Job tracking (MongoDB job queue) */
export interface AssessmentJob {
  job_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED';
  farmer_id?: string;
  result?: AssessmentPayload;
  error?: string;
  created_at?: string;
  updated_at?: string;
}
