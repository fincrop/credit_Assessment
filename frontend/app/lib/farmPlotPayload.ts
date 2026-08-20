/**
 * Build a plot-scoped AssessmentPayload for farm detail tabs.
 * Prefers farm_assessments[].detail (new runs); falls back to farmer-level
 * shared blocks and slim farm fields for older payloads.
 */

import type { AssessmentPayload, FarmAssessment } from '../types/assessment';

export function buildFarmPlotPayload(
  farmerPayload: AssessmentPayload,
  farm: FarmAssessment | null
): AssessmentPayload {
  const d = farm?.detail;

  const cropCycles =
    d?.crop_cycles ||
    (farm?.season_types?.length
      ? {
          detected: true,
          cycles_count: farm.season_types.length,
          method: 'season_types_from_slim',
          cycles: farm.season_types.map((st) => ({
            season_type: st,
            season_label: st,
          })),
          utilization_metrics: {},
        }
      : undefined);

  return {
    ...farmerPayload,
    // Prefer plot detail; else keep farmer-level (weather often shared)
    cropping_analysis: d?.cropping_analysis ?? farmerPayload.cropping_analysis,
    performance_analysis:
      d?.performance_analysis ?? farmerPayload.performance_analysis,
    weather_analysis: d?.weather_analysis ?? farmerPayload.weather_analysis,
    weather_intervals: d?.weather_intervals ?? farmerPayload.weather_intervals,
    crop_cycles: cropCycles ?? farmerPayload.crop_cycles,
    continuous_data_stats:
      d?.continuous_data_stats ?? farmerPayload.continuous_data_stats,
    location: d?.location ?? farmerPayload.location,
    field_area_ha: farm?.area_ha ?? farmerPayload.field_area_ha,
    crop_hint: farm?.crop ?? farmerPayload.crop_hint,
    land_cover: d?.land_cover ?? farm?.land_cover ?? farmerPayload.land_cover,
    parcel_viability:
      d?.parcel_viability ?? farm?.parcel_viability ?? farmerPayload.parcel_viability,
    ai_enrichment: d?.ai_enrichment ?? farmerPayload.ai_enrichment,
    // Plot-scoped risk view via farmer_level shim
    farmer_level: farm
      ? ({
          ...(farmerPayload.farmer_level || {}),
          index_score: farm.index_score ?? null,
          raw_index: farm.raw_index ?? null,
          risk_category: farm.risk_category || null,
          confidence_gate: farm.confidence_gate ?? null,
          sub_indices: farm.sub_indices || {},
          weights: farmerPayload.farmer_level?.weights || {},
          reason_codes: farm.reason_codes || [],
          n_plots_scored: farm.included && farm.index_score != null ? 1 : 0,
          n_plots_total: 1,
        } as AssessmentPayload['farmer_level'])
      : farmerPayload.farmer_level,
    farm_assessments: farm ? [farm] : farmerPayload.farm_assessments,
  };
}

export function plotHasAnalysisDetail(farm: FarmAssessment | null): boolean {
  const d = farm?.detail;
  if (!d) return false;
  return Boolean(
    d.cropping_analysis ||
      d.performance_analysis ||
      d.weather_analysis ||
      d.crop_cycles ||
      d.continuous_data_stats
  );
}
