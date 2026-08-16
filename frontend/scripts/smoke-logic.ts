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
import { terminalStateOf, terminalStateOfFarm, landCoverLabel } from '../app/lib/terminalState';
import { bandForIndex, scoreColor, bandForRiskCategory, toKbsScore } from '../app/lib/kbsScore';

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
t('null -> PENDING', terminalStateOf(null).state, 'PENDING');
t('refusal is never scorable',
  terminalStateOf({ status: 'INSUFFICIENT_DATA' } as never).scorable, false);

// --- slim per-plot records: the prefix is load-bearing ---
t('plot not_agricultural',
  terminalStateOfFarm({ skipped_reason: 'not_agricultural:BUILTUP' } as never).state, 'NOT_FARMLAND');
t('plot insufficient',
  terminalStateOfFarm({ skipped_reason: 'insufficient_observation' } as never).state, 'UNOBSERVED');
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

console.log(failures === 0 ? '\nall checks pass' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
