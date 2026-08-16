/**
 * Adapter: report payload → RiskView.
 *
 * The report contract shapes sub-indices as a list of {key, score, weight,
 * caption}; the dashboard's RiskView uses parallel maps. Rather than write a
 * second score panel against the report shape, the payload is adapted once
 * here and `ScoreWaterfall` is reused verbatim.
 *
 * That is deliberate. Two components rendering the same arithmetic is how the
 * four colour ramps happened, and a report that disagreed with the dashboard
 * about how a score was built would be worse than either — it is the artefact
 * that leaves the building.
 */

import type { RiskView } from './useRiskView';
import type { ReportPayload } from '../types/report';

export function riskViewFromReport(report: ReportPayload): RiskView {
  const subIndices: Record<string, number> = {};
  const weights: Record<string, number> = {};
  const weak: string[] = [];

  for (const s of report.sub_indices ?? []) {
    if (typeof s.score === 'number' && Number.isFinite(s.score)) {
      subIndices[s.key] = s.score;
    }
    if (typeof s.weight === 'number' && Number.isFinite(s.weight)) {
      weights[s.key] = s.weight;
    }
    if (s.is_weakest) weak.push(s.key);
  }

  return {
    score: report.score?.index_score ?? null,
    rawIndex: report.score?.raw_index ?? null,
    category: report.score?.risk_category ?? null,
    subIndices,
    weights,
    gate: report.score?.confidence_gate ?? null,
    reasonCodes: report.reason_codes ?? [],
    weakSubIndices: weak,
    // The report payload carries no benefits block — the bonus is already
    // folded into raw_index upstream. Reporting a bonus we cannot see would
    // put a number in the waterfall that nothing supports.
    benefits: { pm_kisan: null, has_crop_insurance: null },
    indexVersion: report.score?.index_version ?? report.methodology?.index_version ?? null,
    method: null,
    source: 'risk_index_v5',
    narrative: report.narrative?.text ?? null,
    narrativeSource: report.narrative?.source ?? null,
    scoringNarrative: null,
    insufficientData: report.score?.kbs == null,
  };
}

/** Captions keyed by sub-index, for the waterfall's per-driver explanations. */
export function captionsFromReport(report: ReportPayload): Record<string, string> {
  const out: Record<string, string> = {};
  for (const s of report.sub_indices ?? []) {
    if (s.caption) out[s.key] = s.caption;
  }
  if (report.data_confidence?.caption) {
    out.data_confidence = report.data_confidence.caption;
  }
  return out;
}
