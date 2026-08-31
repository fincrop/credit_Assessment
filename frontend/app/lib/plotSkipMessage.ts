/**
 * Human-readable skip explanations aligned with backend parcel policy.
 * PARCEL_MIN_PIXELS_HARD = 15 → 0.15 ha fundable / monitoring floor.
 */

import type { FarmAssessment } from '../types/assessment';
import { formatHa } from './areaMismatch';

export const SENTINEL_PIXEL_HA = 0.01;
export const MONITORING_MIN_HA = 0.15;

export function skipReasonToken(farm: FarmAssessment): string {
  return String(farm.skipped_reason || '');
}

/** Plot was skipped because the measurable footprint is below the lending floor. */
export function isMonitoringAreaTooSmall(
  farm: FarmAssessment,
  measuredHa?: number | null
): boolean {
  const token = skipReasonToken(farm);
  if (token.includes('too_small')) return true;

  const pv = farm.parcel_viability;
  if (pv?.outcome !== 'not_viable') return false;

  const ev = pv.evidence;
  const hardPx = ev?.thresholds?.min_pixels_hard ?? 15;
  const px = ev?.approx_pixels;
  if (typeof px === 'number' && px < hardPx) return true;

  const effective =
    ev?.effective_ha ?? ev?.geometry_ha ?? measuredHa ?? farm.measured_area_ha;
  if (typeof effective === 'number' && effective > 0 && effective < MONITORING_MIN_HA) {
    return true;
  }

  return pv.outcome === 'not_viable';
}

export function monitoringAreaTooSmallMessages(
  farm: FarmAssessment,
  measuredHa?: number | null
): { detail: string; skip: string } {
  const pv = farm.parcel_viability;
  const ev = pv?.evidence;
  const effective =
    ev?.effective_ha ?? ev?.geometry_ha ?? measuredHa ?? farm.measured_area_ha ?? null;
  const px = ev?.approx_pixels;
  const minHa =
    (ev?.thresholds?.min_pixels_hard ?? 15) * SENTINEL_PIXEL_HA;

  let detail =
    pv?.reason ||
    farm.data_sufficiency?.reason ||
    (typeof effective === 'number'
      ? `Monitoring area is about ${formatHa(effective)}${
          typeof px === 'number' ? ` (~${px} Sentinel-2 pixels at 10 m)` : ''
        }.`
      : 'Monitoring area is below our minimum for reliable satellite analysis.');

  const skip = `Skipped — below our ${formatHa(minHa, 2)} minimum monitoring area for scoring.`;

  return { detail, skip };
}

export function isAreaMismatchSkip(farm: FarmAssessment): boolean {
  const token = skipReasonToken(farm);
  return token.includes('area_mismatch');
}
