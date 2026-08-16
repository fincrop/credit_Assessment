/**
 * Terminal state — which screen a payload gets, decided in one place.
 *
 * The backend distinguishes three outcomes and went to real trouble to keep
 * them apart. Collapsing them in the UI throws away the most defensible thing
 * about this product:
 *
 *   SCORED       we looked; here is the score
 *   NOT_FARMLAND we looked; it is not farmland
 *   UNOBSERVED   we could not see it well enough to say
 *   FAILED       something broke on our side
 *
 * The distinction that matters most is UNOBSERVED vs a low score. A parcel
 * under cloud for a quarter is not a bad farm. Showing it as one — or as a
 * generic error — tells a lender something false about a real person.
 */

import type {
  AssessmentPayload,
  FarmAssessment,
  LandCover,
  DataSufficiency,
  ParcelViability,
} from '../types/assessment';

export type TerminalState = 'SCORED' | 'NOT_FARMLAND' | 'UNOBSERVED' | 'FAILED' | 'PENDING';

export interface TerminalVerdict {
  state: TerminalState;
  /** The backend's own words. Never paraphrase a refusal into something softer. */
  reason: string | null;
  landCover: LandCover | null;
  dataSufficiency: DataSufficiency | null;
  parcelViability: ParcelViability | null;
  /** True when a score exists and may be rendered. */
  scorable: boolean;
}

const EMPTY: TerminalVerdict = {
  state: 'PENDING',
  reason: null,
  landCover: null,
  dataSufficiency: null,
  parcelViability: null,
  scorable: false,
};

export function terminalStateOf(
  data: AssessmentPayload | null | undefined
): TerminalVerdict {
  if (!data) return EMPTY;

  const status = String(data.status || '').toUpperCase();
  const landCover = data.land_cover ?? null;
  const dataSufficiency = data.data_sufficiency ?? null;
  const parcelViability = data.parcel_viability ?? null;

  if (status === 'REJECTED_NOT_AGRICULTURAL') {
    return {
      state: 'NOT_FARMLAND',
      reason: data.rejection_reason ?? landCover?.reason ?? null,
      landCover,
      dataSufficiency,
      parcelViability,
      scorable: false,
    };
  }

  if (status === 'INSUFFICIENT_DATA') {
    return {
      state: 'UNOBSERVED',
      // Viability failures set insufficient_reason too, so prefer it — it is
      // the specific cause (too small / cloud-gapped / untrusted boundary)
      // rather than the generic category.
      reason:
        data.insufficient_reason ??
        dataSufficiency?.reason ??
        parcelViability?.reason ??
        null,
      landCover,
      dataSufficiency,
      parcelViability,
      scorable: false,
    };
  }

  if (status === 'FAILED') {
    return {
      state: 'FAILED',
      reason: data.error ?? null,
      landCover,
      dataSufficiency,
      parcelViability,
      scorable: false,
    };
  }

  const hasScore =
    typeof data.risk_assessment?.index_score === 'number' ||
    typeof data.farmer_level?.index_score === 'number';

  return {
    state: hasScore ? 'SCORED' : 'PENDING',
    reason: null,
    landCover,
    dataSufficiency,
    parcelViability,
    scorable: hasScore,
  };
}

/**
 * Same decision for a slim per-plot record inside a multi-farm result.
 *
 * The prefix on `skipped_reason` is load-bearing: `counters_from_farm_assessments`
 * buckets on it, and only `error:` counts as a failure. A plot excluded because
 * it is a pond is a legitimate exclusion, not a broken plot.
 */
export function terminalStateOfFarm(farm: FarmAssessment | null | undefined): TerminalVerdict {
  if (!farm) return EMPTY;

  const skipped = String(farm.skipped_reason || '');

  if (skipped.startsWith('not_agricultural:')) {
    return {
      state: 'NOT_FARMLAND',
      reason: farm.land_cover?.reason ?? null,
      landCover: (farm.land_cover as LandCover) ?? null,
      dataSufficiency: null,
      parcelViability: null,
      scorable: false,
    };
  }

  if (skipped.startsWith('insufficient_observation')) {
    return {
      state: 'UNOBSERVED',
      reason: farm.data_sufficiency?.reason ?? null,
      landCover: null,
      dataSufficiency: farm.data_sufficiency
        ? { reason: farm.data_sufficiency.reason, evidence: farm.data_sufficiency }
        : null,
      parcelViability: null,
      scorable: false,
    };
  }

  if (skipped.startsWith('error:')) {
    return { ...EMPTY, state: 'FAILED', reason: skipped.slice('error:'.length).trim() || null };
  }

  const hasScore = typeof farm.index_score === 'number';
  return {
    ...EMPTY,
    state: hasScore ? 'SCORED' : skipped ? 'FAILED' : 'PENDING',
    reason: skipped || null,
    scorable: hasScore,
  };
}

/** Land-cover classes rendered with their plain-English meaning. */
export const LAND_COVER_LABELS: Record<string, string> = {
  CROPLAND: 'Cropland',
  WATER: 'Water body',
  BUILTUP: 'Built-up land',
  BARREN: 'Barren land',
  FOREST: 'Forest',
  PLANTATION: 'Plantation / orchard',
  UNKNOWN: 'Unclassified',
};

export function landCoverLabel(cls: string | null | undefined): string {
  if (!cls) return 'Unclassified';
  return LAND_COVER_LABELS[String(cls).toUpperCase()] ?? String(cls);
}
