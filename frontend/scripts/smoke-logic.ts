/**
 * Smoke tests for the pure decision logic — no test runner, no DOM.
 *
 *   npm run smoke
 *
 * These cover the two things that are silently wrong when they break: which
 * terminal state a payload resolves to (a refusal misread as a low score is
 * the worst failure this product has), and whether every consumer agrees on
 * the colour of a given index. Both were real defects; both are cheap to
 * re-break. Exits non-zero on any failure so CI can gate on it.
 */
import { terminalStateOf, terminalStateOfFarm, terminalStateOfReport, landCoverLabel } from '../app/lib/terminalState';
import { bandForIndex, scoreColor, bandForRiskCategory, toKbsScore } from '../app/lib/kbsScore';
import { linePath, segments, linearScale, ticks, areaPath } from '../app/lib/chart';
import { areaMismatchOf, formatHa } from '../app/lib/areaMismatch';
import { inferCyclesFromNdvi, resolveCropCycles } from '../app/lib/ndviCycles';

let failures = 0;
const t = (name: string, got: unknown, want: unknown) => {
  const pass = JSON.stringify(got) === JSON.stringify(want);
  if (!pass) failures += 1;
  console.log(
    `${pass ? 'ok  ' : 'FAIL'} ${name}` +
      (pass ? '' : `  got=${JSON.stringify(got)} want=${JSON.stringify(want)}`)
  );
};

// --- terminal states ---
t('reject -> NOT_FARMLAND',
  terminalStateOf({ status: 'REJECTED_NOT_AGRICULTURAL', rejection_reason: 'open water',
                    land_cover: { class: 'WATER', confidence: 0.91 } } as never).state, 'NOT_FARMLAND');
t('reject keeps backend reason',
  terminalStateOf({ status: 'REJECTED_NOT_AGRICULTURAL', rejection_reason: 'open water' } as never).reason, 'open water');
t('insufficient -> UNOBSERVED',
  terminalStateOf({ status: 'INSUFFICIENT_DATA', insufficient_reason: '4 px' } as never).state, 'UNOBSERVED');
t('insufficient prefers specific cause',
  terminalStateOf({ status: 'INSUFFICIENT_DATA', insufficient_reason: 'too small',
                    data_sufficiency: { reason: 'generic' } } as never).reason, 'too small');
t('failed -> FAILED', terminalStateOf({ status: 'FAILED', error: 'boom' } as never).state, 'FAILED');
t('scored -> SCORED',
  terminalStateOf({ status: 'SUCCESS', risk_assessment: { index_score: 61 } } as never).state, 'SCORED');
t('success w/o score -> PENDING', terminalStateOf({ status: 'SUCCESS' } as never).state, 'PENDING');
t('report reject -> NOT_FARMLAND',
  terminalStateOfReport({ status: 'REJECTED_NOT_AGRICULTURAL',
    land_cover: { class: 'WATER', reason: 'open water' } } as never).state, 'NOT_FARMLAND');
t('report insufficient -> UNOBSERVED',
  terminalStateOfReport({ status: 'INSUFFICIENT_DATA',
    parcel_viability: { reason: '5 px' } } as never).state, 'UNOBSERVED');
t('report scored -> SCORED',
  terminalStateOfReport({ status: 'SUCCESS', score: { kbs: 592, index_score: 48.7 } } as never).state, 'SCORED');
t('null -> PENDING', terminalStateOf(null).state, 'PENDING');
t('refusal is never scorable',
  terminalStateOf({ status: 'INSUFFICIENT_DATA' } as never).scorable, false);

// --- slim per-plot records: the prefix is load-bearing ---
t('plot not_agricultural',
  terminalStateOfFarm({ skipped_reason: 'not_agricultural:BUILTUP' } as never).state, 'NOT_FARMLAND');
t('plot insufficient',
  terminalStateOfFarm({ skipped_reason: 'insufficient_observation' } as never).state, 'UNOBSERVED');
t('plot area mismatch still UNOBSERVED',
  terminalStateOfFarm({ skipped_reason: 'insufficient_observation:area_mismatch' } as never).state, 'UNOBSERVED');
t('plot error',
  terminalStateOfFarm({ skipped_reason: 'error: ee timeout' } as never).state, 'FAILED');
t('plot error strips prefix',
  terminalStateOfFarm({ skipped_reason: 'error: ee timeout' } as never).reason, 'ee timeout');
t('plot scored', terminalStateOfFarm({ index_score: 55 } as never).state, 'SCORED');
t('landCoverLabel', landCoverLabel('BUILTUP'), 'Built-up land');
t('landCoverLabel unknown passthrough', landCoverLabel('MANGROVE'), 'MANGROVE');

// --- one mapping: band boundaries must line up with KBS quartiles ---
t('index 0   -> Poor',       bandForIndex(0)?.name, 'Poor');
t('index 24.9-> Poor',       bandForIndex(24.9)?.name, 'Poor');
t('index 25  -> Fair',       bandForIndex(25)?.name, 'Fair');
t('index 50  -> Good',       bandForIndex(50)?.name, 'Good');
t('index 68  -> Good',       bandForIndex(68)?.name, 'Good');   // the disputed value
t('index 75  -> Excellent',  bandForIndex(75)?.name, 'Excellent');
t('index 100 -> Excellent',  bandForIndex(100)?.name, 'Excellent');
t('kbs of 68', toKbsScore(68), 708);
t('null index -> no band',   bandForIndex(null), null);
t('null index -> grey',      scoreColor(null), '#A8A29E');

// the whole point of item 1: every consumer agrees on 68
t('68 colour == Good colour', scoreColor(68), bandForIndex(68)!.color);

// --- risk category mapping, incl. the VERY_HIGH substring trap ---
t('LOW -> Excellent',       bandForRiskCategory('LOW')?.name, 'Excellent');
t('MEDIUM -> Good',         bandForRiskCategory('MEDIUM')?.name, 'Good');
t('HIGH -> Fair',           bandForRiskCategory('HIGH')?.name, 'Fair');
t('VERY_HIGH -> Poor',      bandForRiskCategory('VERY_HIGH')?.name, 'Poor');
t('VERY HIGH (space)',      bandForRiskCategory('VERY HIGH')?.name, 'Poor');
t('unknown cat -> null',    bandForRiskCategory('INSUFFICIENT_DATA'), null);

/* ── Score build-up arithmetic (ScoreWaterfall) ──────────────────────────
   Mirrors risk_index_engine.py:
     additive  = Σ (score_i × weight_i / 100)
     raw_index = clip(additive + benefits.bonus)
     index     = clip(raw_index × gate)
   If the panel's arithmetic drifts from the engine's, the waterfall shows a
   reader a derivation that did not happen. */
const contribution = (score: number, weight: number) => (score * weight) / 100;

const SUBS = { landuse: 71, vigor: 42, stability: 65.5, weather: 56 };
const W = { landuse: 30, vigor: 35, stability: 20, weather: 15 };
const additive =
  contribution(SUBS.landuse, W.landuse) +
  contribution(SUBS.vigor, W.vigor) +
  contribution(SUBS.stability, W.stability) +
  contribution(SUBS.weather, W.weather);

t('weights sum to 100', W.landuse + W.vigor + W.stability + W.weather, 100);
// 21.3 + 14.7 + 13.1 + 8.4
t('additive composite', Number(additive.toFixed(1)), 57.5);
t('gated index', Number((additive * 0.924).toFixed(1)), 53.1);
t('gate loss is raw minus index',
  Number((additive - additive * 0.924).toFixed(1)), 4.4);

// Headroom is the number a loan officer acts on: points still available.
t('weakest driver has most headroom',
  Number((W.vigor - contribution(SUBS.vigor, W.vigor)).toFixed(1)), 20.3);
t('headroom + contribution == weight',
  Number((contribution(SUBS.vigor, W.vigor) + (W.vigor - contribution(SUBS.vigor, W.vigor))).toFixed(1)),
  W.vigor);

// A perfect holding must reach exactly the top of the scale, or the bar
// silently implies unreachable headroom.
t('all-100 reaches the index ceiling',
  contribution(100, W.landuse) + contribution(100, W.vigor) +
    contribution(100, W.stability) + contribution(100, W.weather), 100);
t('index 100 maps to KBS max', toKbsScore(100), 900);

/* -- Chart primitives: the rules that keep the trajectory honest ---------
   A line drawn across a bin with no observation asserts a measurement that
   was never taken. This is the single most important property of the NDVI
   chart, and it is one `M` vs `L` away from being silently wrong. */
const withGap = [
  { x: 0, y: 10 },
  { x: 10, y: 20 },
  { x: 20, y: null },
  { x: 30, y: 40 },
];
const d = linePath(withGap);
t('gap starts a new subpath, not a bridge', (d.match(/M/g) || []).length, 2);
t('no segment spans the gap', d.includes('L30.00'), false);
t('all-null series draws nothing', linePath([{ x: 0, y: null }]), '');
t('NaN counts as no observation', linePath([{ x: 0, y: NaN }, { x: 1, y: 5 }]).startsWith('M1.00'), true);

t('segments splits on the gap', segments(withGap).length, 2);
t('segments drop the null', segments(withGap).flat().length, 3);
t('empty in, empty out', segments([]).length, 0);

/* An area fill must close to the baseline, never to the previous point --
   otherwise the shaded region implies coverage across a gap. */
const ap = areaPath([{ x: 0, y: 10 }, { x: 10, y: 20 }], 100);
t('area closes to baseline', ap.endsWith('Z'), true);
t('area returns along the baseline', ap.includes('L10.00 100.00'), true);

/* Scales */
const sc = linearScale([0, 100], [0, 200]);
t('scale maps domain start', sc(0), 0);
t('scale maps domain end', sc(100), 200);
t('scale is linear', sc(25), 50);
t('scale inverts', sc.invert(50), 25);
t('inverted range works (y axis)', linearScale([0, 1], [200, 0])(1), 0);
t('zero-width domain does not divide by zero', Number.isFinite(linearScale([5, 5], [0, 10])(5)), true);

// Float accumulation used to emit 0.6000000000000001 here, which reaches an
// axis as a label unless every caller remembers to format it.
t('ticks land on round numbers', ticks([0, 1], 4).join(','), '0,0.2,0.4,0.6,0.8,1');
t('ticks handle a flat domain', ticks([3, 3]).length, 1);
t('ticks cover an integer domain', ticks([0, 100], 5).join(','), '0,20,40,60,80,100');
t('ticks respect a non-zero start', ticks([12, 30], 3).join(','), '15,20,25,30');

const mismatch = areaMismatchOf({ registeredHa: 0.67, measuredHa: 0.04 });
t('agristack vs mapped mismatch detected', mismatch != null, true);
t('mismatch flags measured much smaller', mismatch?.measuredMuchSmaller, true);
t('matching areas are not flagged', areaMismatchOf({ registeredHa: 1.2, measuredHa: 1.18 }), null);
t('formatHa keeps tiny plots precise', formatHa(0.004), '0.004 ha');

/* Modest NDVI ~0.5 crops must still count as growing seasons. */
function isoDaysFrom(start: string, n: number, step = 10): string[] {
  const t0 = new Date(start).getTime();
  return Array.from({ length: n }, (_, i) =>
    new Date(t0 + i * step * 86400000).toISOString().slice(0, 10)
  );
}
function hat(peak: number, length: number, base = 0.22): number[] {
  const half = Math.floor(length / 2);
  const up = Array.from({ length: half }, (_, i) => base + ((peak - base) * i) / Math.max(1, half - 1));
  const down = Array.from(
    { length: length - half },
    (_, i) => peak + ((base - peak) * i) / Math.max(1, length - half - 1)
  );
  return [...up, ...down];
}
const modestProfile = [
  ...Array(5).fill(0.22),
  ...hat(0.50, 12),
  ...Array(6).fill(0.20),
  ...hat(0.48, 11),
  ...Array(6).fill(0.21),
  ...hat(0.52, 12),
  ...Array(5).fill(0.20),
];
const modestTraj = { dates: isoDaysFrom('2023-06-15', modestProfile.length), ndvi: modestProfile, comparison_available: false };
const modestCycles = inferCyclesFromNdvi(modestTraj);
t('NDVI ~0.5 seasons are detected', modestCycles.length >= 3, true);

const mixedProfile = [
  ...Array(3).fill(0.22),
  ...hat(0.74, 14, 0.20),
  ...Array(5).fill(0.22),
  ...hat(0.50, 11, 0.20),
  ...Array(5).fill(0.20),
  ...hat(0.70, 14, 0.20),
  ...Array(5).fill(0.21),
  ...hat(0.48, 11, 0.20),
  ...Array(5).fill(0.20),
  ...hat(0.72, 14, 0.20),
  ...Array(5).fill(0.21),
  ...hat(0.52, 11, 0.20),
  ...Array(4).fill(0.20),
];
const mixedTraj = { dates: isoDaysFrom('2023-06-15', mixedProfile.length), ndvi: mixedProfile, comparison_available: false };
const mixedCycles = inferCyclesFromNdvi(mixedTraj);
t('kharif + modest rabi are both found', mixedCycles.length >= 5, true);
t('resolveCropCycles prefers richer NDVI read', resolveCropCycles([], mixedTraj).length >= 5, true);
t('barren wobble is not a season', inferCyclesFromNdvi({
  dates: isoDaysFrom('2023-06-15', 40),
  ndvi: Array.from({ length: 40 }, (_, i) => 0.12 + 0.012 * ((i * 7) % 5)),
  comparison_available: false,
}).length, 0);

console.log(failures === 0 ? '\nall checks pass' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
