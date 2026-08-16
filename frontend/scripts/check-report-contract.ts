/**
 * Contract check: types/report.ts vs what the backend actually emits.
 *
 *   npm run check:contract
 *
 * `scripts/report_sample.json` is a real payload produced by calling
 * `build_report_payload` directly (see the header of that file's generator in
 * the commit that added it). A hand-written mirror of a Python dict drifts
 * within a sprint; this fails the build when it does.
 *
 * Reports BOTH directions, because they are different bugs:
 *   missing  — backend emits a key we do not type. We silently ignore data.
 *   extra    — we type a key the backend never sends. We render undefined.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ReportResponse } from '../app/types/report';
import { hasSection, trendLabel } from '../app/lib/reportClient';
import { riskViewFromReport, captionsFromReport } from '../app/lib/reportView';

const raw = JSON.parse(
  readFileSync(join(import.meta.dirname, 'report_sample.json'), 'utf8')
) as ReportResponse;

const report = raw.report;
let failures = 0;

const check = (name: string, ok: boolean, detail = '') => {
  if (!ok) failures += 1;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : `  ${detail}`}`);
};

// Keys we expect on the top level of the payload, from types/report.ts.
const TYPED_TOP = [
  'payload_version', 'generated_at', 'farmer_id', 'assessment_date', 'report_id',
  'status', 'farmer', 'prepared_by', 'score', 'trend', 'sub_indices',
  'data_confidence', 'reason_codes', 'ndvi_trajectory', 'land_cover',
  'parcel_viability', 'data_sufficiency', 'crop_verification', 'footprint',
  'narrative', 'methodology', 'omitted', 'integrity', 'sections_present',
];

const actual = Object.keys(report);
const missing = actual.filter((k) => !TYPED_TOP.includes(k));
const extra = TYPED_TOP.filter((k) => !actual.includes(k));

check('no untyped keys from backend', missing.length === 0, `untyped: ${missing.join(', ')}`);
check('no phantom keys in our type', extra.length === 0, `we type but backend omits: ${extra.join(', ')}`);

// Structural expectations the renderer depends on.
check('payload_version pinned', report.payload_version === 'report_payload_v1', report.payload_version);
check('score.positioning is not a loan amount',
  report.score.positioning === 'agronomic_risk_index', String(report.score.positioning));
check('no_repayment_calibration true', report.score.no_repayment_calibration === true);
check('KBS scale is 300-900',
  report.score.scale_min === 300 && report.score.scale_max === 900,
  `${report.score.scale_min}-${report.score.scale_max}`);
check('four bands', report.score.bands.length === 4, String(report.score.bands.length));
check('band names match the gauge',
  report.score.bands.map((b) => b.name).join(',') === 'Poor,Fair,Good,Excellent',
  report.score.bands.map((b) => b.name).join(','));

// The frontend maps index -> KBS itself; both sides must agree or the gauge
// and the report show different numbers for the same assessment.
check('backend KBS matches our mapping',
  report.score.kbs === Math.round(300 + (report.score.index_score! / 100) * 600),
  `backend=${report.score.kbs}`);

check('sub_indices carry captions',
  report.sub_indices.length > 0 && report.sub_indices.every((s) => typeof s.caption === 'string'),
  JSON.stringify(report.sub_indices.map((s) => s.caption)));
check('weakest sub-index flagged',
  report.sub_indices.some((s) => s.is_weakest));

check('ndvi trajectory refuses a peer line',
  report.ndvi_trajectory?.comparison_available === false);
check('and says why',
  typeof report.ndvi_trajectory?.comparison_note === 'string' &&
    report.ndvi_trajectory.comparison_note.length > 0);
check('trajectory dates and values align',
  report.ndvi_trajectory!.dates.length === report.ndvi_trajectory!.ndvi.length);

check('omitted names the panels we must not build',
  ['policy_action', 'peer_comparison', 'review_workflow'].every((k) => k in report.omitted),
  Object.keys(report.omitted).join(','));

check('integrity hash present',
  typeof report.integrity?.content_sha256 === 'string' &&
    report.integrity.content_sha256.length === 64);

check('footprint substitution survives the payload',
  report.footprint?.geometry_substituted === true);

check('trend present with history', report.trend !== null);
check('trend direction is up', report.trend?.direction === 'up', String(report.trend?.direction));

check('sections_present covers every gated section',
  ['score', 'trend', 'sub_indices', 'ndvi_trajectory', 'land_cover',
   'crop_verification', 'narrative'].every((k) => k in (report.sections_present ?? {})));

/* ── First assessment: the payload must REFUSE to invent a baseline ──────
   `_trend` returns None rather than a delta of 0, because a zero against a
   baseline that does not exist is a fabricated history. The UI contract that
   depends on it: when trend is null there is NO chip, not one reading "—". */
const first = (
  JSON.parse(
    readFileSync(join(import.meta.dirname, 'report_sample_first.json'), 'utf8')
  ) as ReportResponse
).report;

check('first assessment emits no trend', first.trend === null, JSON.stringify(first.trend));
check('trendLabel yields nothing to render', trendLabel(first) === null, String(trendLabel(first)));
check('no evidence -> no trajectory, not an empty one', first.ndvi_trajectory === null);
check('sections_present gates the empty ones',
  first.sections_present?.trend === false &&
    first.sections_present?.ndvi_trajectory === false &&
    first.sections_present?.narrative === false,
  JSON.stringify(first.sections_present));
check('hasSection agrees with sections_present',
  !hasSection(first, 'trend') && !hasSection(first, 'narrative') && hasSection(first, 'score'));
check('score still renders', first.score.kbs !== null, String(first.score.kbs));

/* `sections_present` is attached by the ENDPOINT, not by build_report_payload,
   so a payload can legitimately arrive without it — an assessment stored
   before it existed, or any other caller of the builder. `hasSection` must
   fall back to the field itself rather than reporting everything absent, or
   an older record renders as a blank report. */
const legacy = { ...first, sections_present: undefined };
check('hasSection falls back when sections_present is absent',
  hasSection(legacy, 'score') && hasSection(legacy, 'sub_indices') &&
    !hasSection(legacy, 'trend') && !hasSection(legacy, 'ndvi_trajectory'),
  'an older payload must still render what it has');

/* ── The report must not disagree with the dashboard ─────────────────────
   Both render the score build-up through ScoreWaterfall, so the report
   payload is adapted to RiskView rather than given its own panel. If this
   adapter drops or reshapes a field, the report and the dashboard show
   different derivations of the same score — and the report is the artefact
   that leaves the building. */
const view = riskViewFromReport(report);

/** Equality check. `check` takes a boolean — passing a raw value would make
 *  any non-empty string pass vacuously, which is exactly what happened when
 *  this block was first written against the smoke suite's signature. */
const eq = (name: string, got: unknown, want: unknown) =>
  check(
    name,
    JSON.stringify(got) === JSON.stringify(want),
    `got=${JSON.stringify(got)} want=${JSON.stringify(want)}`
  );

eq('adapter carries every sub-index',
  Object.keys(view.subIndices).sort(),
  report.sub_indices.filter((s) => s.score != null).map((s) => s.key).sort());
eq('adapter carries the weights',
  Object.keys(view.weights).length, report.sub_indices.filter((s) => s.weight != null).length);
eq('adapter preserves the index', view.score, report.score.index_score);
eq('adapter preserves the raw index', view.rawIndex, report.score.raw_index);
eq('adapter preserves the gate', view.gate, report.score.confidence_gate);
eq('adapter flags the weakest driver',
  view.weakSubIndices, report.sub_indices.filter((s) => s.is_weakest).map((s) => s.key));
eq('adapter carries reason codes', view.reasonCodes.length, report.reason_codes.length);
eq('adapter reports no score as insufficient',
  riskViewFromReport({ ...report, score: { ...report.score, kbs: null } }).insufficientData, true);

// The waterfall recomputes the composite from these; it must land on the
// index the backend already published, or the report shows a derivation
// that did not happen.
const composite = Object.keys(view.subIndices).reduce(
  (a, k) => a + (view.subIndices[k] * (view.weights[k] ?? 0)) / 100,
  0
);
check('adapted parts reproduce the backend raw index',
  Math.abs(composite - (view.rawIndex ?? 0)) < 0.15,
  `parts=${composite.toFixed(2)} backend raw=${view.rawIndex}`);

const caps = captionsFromReport(report);
check('captions extracted per driver',
  report.sub_indices.every((s) => !s.caption || caps[s.key] === s.caption));
check('data-confidence caption carried',
  report.data_confidence.caption ? caps.data_confidence === report.data_confidence.caption : true);

console.log(failures === 0 ? '\ncontract holds' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
