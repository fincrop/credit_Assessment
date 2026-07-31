'use client';

import { useMemo } from 'react';
import type {
  AssessmentPayload,
  ReasonCode,
  RiskCategory,
  TriState,
} from '../types/assessment';
import { resolveWeights, SUBSTANTIVE_SUBINDEX_KEYS } from './formatRisk';

export type RiskViewSource = 'risk_index_v5' | 'legacy_shim';

export interface RiskView {
  score: number | null;
  rawIndex: number | null;
  category: RiskCategory | null;
  subIndices: Record<string, number>;
  weights: Record<string, number>;
  gate: number | null;
  reasonCodes: ReasonCode[];
  weakSubIndices: string[];
  benefits: {
    pm_kisan: TriState;
    has_crop_insurance: TriState;
    bonus?: number;
  };
  indexVersion: string | null;
  method: string | null;
  source: RiskViewSource;
  narrative: string | null;
  narrativeSource: string | null;
  scoringNarrative: string | null;
}

function scalarSubScore(val: unknown): number | null {
  if (typeof val === 'number' && Number.isFinite(val)) return val;
  if (val && typeof val === 'object' && 'score' in (val as object)) {
    const s = (val as { score?: unknown }).score;
    if (typeof s === 'number' && Number.isFinite(s)) return s;
  }
  return null;
}

export function buildRiskView(data: AssessmentPayload | null | undefined): RiskView {
  const empty: RiskView = {
    score: null,
    rawIndex: null,
    category: null,
    subIndices: {},
    weights: resolveWeights(null),
    gate: null,
    reasonCodes: [],
    weakSubIndices: [],
    benefits: { pm_kisan: null, has_crop_insurance: null },
    indexVersion: null,
    method: null,
    source: 'legacy_shim',
    narrative: null,
    narrativeSource: null,
    scoringNarrative: null,
  };

  if (!data) return empty;

  const ra = data.risk_assessment;
  const ca = data.credit_assessment;
  const ai = data.ai_enrichment;

  const subIndices: Record<string, number> = {};
  if (ra?.sub_indices) {
    for (const [k, v] of Object.entries(ra.sub_indices)) {
      const s = scalarSubScore(v);
      if (s != null) subIndices[k] = s;
    }
  } else if (ca?.component_scores) {
    for (const [k, v] of Object.entries(ca.component_scores)) {
      if (typeof v === 'number' && Number.isFinite(v)) subIndices[k] = v;
    }
  }

  // Prefer substantive keys order for display consumers
  const ordered: Record<string, number> = {};
  for (const k of SUBSTANTIVE_SUBINDEX_KEYS) {
    if (subIndices[k] != null) ordered[k] = subIndices[k];
  }
  for (const [k, v] of Object.entries(subIndices)) {
    if (!(k in ordered) && k !== 'data_confidence') ordered[k] = v;
  }

  const weights = resolveWeights(ra?.weights ?? ca?.component_weights ?? null);

  const score =
    (typeof ra?.index_score === 'number' ? ra.index_score : null) ??
    (typeof ca?.credit_score === 'number' ? ca.credit_score : null) ??
    (typeof data.summary?.credit_score === 'number'
      ? (data.summary.credit_score as number)
      : null);

  const rawIndex = typeof ra?.raw_index === 'number' ? ra.raw_index : null;

  const category =
    ra?.risk_category ??
    ca?.risk_category ??
    (typeof data.summary?.risk_category === 'string'
      ? data.summary.risk_category
      : null);

  const gate =
    (typeof ra?.confidence_gate === 'number' ? ra.confidence_gate : null) ??
    (typeof ca?.confidence_gate === 'number' ? ca.confidence_gate : null) ??
    (typeof ca?.confidence === 'number' ? ca.confidence : null);

  const reasonCodes = (ra?.reason_codes ?? ca?.reason_codes ?? []).filter(Boolean);

  const weakSubIndices = (ra?.weak_sub_indices ?? ca?.weak_components ?? []).map(
    String
  );

  // A5: engine-preserved tri-state first
  const pm: TriState =
    ra?.benefits?.pm_kisan !== undefined
      ? ra.benefits.pm_kisan
      : data.farmer_benefits?.pm_kisan_enrolled !== undefined
        ? data.farmer_benefits.pm_kisan_enrolled
        : null;
  const ins: TriState =
    ra?.benefits?.has_crop_insurance !== undefined
      ? ra.benefits.has_crop_insurance
      : data.farmer_benefits?.has_crop_insurance !== undefined
        ? data.farmer_benefits.has_crop_insurance
        : null;

  return {
    score,
    rawIndex,
    category,
    subIndices: ordered,
    weights,
    gate,
    reasonCodes,
    weakSubIndices,
    benefits: {
      pm_kisan: pm,
      has_crop_insurance: ins,
      bonus: ra?.benefits?.bonus,
    },
    indexVersion:
      ra?.index_version ?? ca?.index_version ?? data.index_version ?? null,
    method: ra?.method ?? ca?.method ?? null,
    source: ra ? 'risk_index_v5' : 'legacy_shim',
    narrative: ai?.english_narrative ?? null,
    narrativeSource: ai?.narrative_source ?? null,
    scoringNarrative: ca?.scoring_narrative ?? null,
  };
}

export function useRiskView(data: AssessmentPayload | null | undefined): RiskView {
  return useMemo(() => buildRiskView(data), [data]);
}
