'use client';

import type { AssessmentPayload, FarmAssessment, ReasonCode } from '../../types/assessment';
import { useRiskView } from '../../lib/useRiskView';
import {
  formatScoreOne,
  resolveWeights,
  subIndexLabel,
  SUBSTANTIVE_SUBINDEX_KEYS,
} from '../../lib/formatRisk';
import {
  toKbsScore,
  kbsBandForScore,
  bandChipStyle,
  scoreColor,
  bandCardSurface,
  KBS_MAX,
} from '../../lib/kbsScore';
import { KbsGauge } from './KbsGauge';

function PillarIcon({ kind }: { kind: string }) {
  const common = 'w-3 h-3 text-stone-500 shrink-0';
  switch (kind) {
    case 'landuse':
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 21s-7-4.5-7-11a7 7 0 1 1 14 0c0 6.5-7 11-7 11z" />
          <circle cx="12" cy="10" r="2.5" />
        </svg>
      );
    case 'vigor':
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M5 19c4-2 6-6 7-12 4 2 6 6 7 12" />
          <path d="M12 7v12" />
        </svg>
      );
    case 'stability':
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 3l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V7l8-4z" />
        </svg>
      );
    case 'weather':
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M7 16a4 4 0 1 1 1.2-7.8A5 5 0 0 1 18 11a3.5 3.5 0 0 1-.2 7H7z" />
          <path d="M9 19v2M12 18v3M15 19v2" />
        </svg>
      );
    default:
      return null;
  }
}

function plotHoldingStats(
  farms: FarmAssessment[] | undefined,
  nTotal: number | null | undefined,
  reasonCodes: ReasonCode[]
) {
  const list = farms || [];
  let total = nTotal ?? (list.length || 0);
  let ownedScored = 0;
  let leasedDown = 0;

  for (const f of list) {
    if (!f.included) continue;
    const leased =
      f.is_ror_owner === false || (typeof f.tenure_factor === 'number' && f.tenure_factor < 1);
    if (leased) leasedDown += 1;
    else if (f.index_score != null) ownedScored += 1;
  }

  for (const r of reasonCodes || []) {
    const msg = String(r?.message || '');
    const code = String(r?.code || '');
    if (code === 'PORTFOLIO_SIZE' || /owned plot/i.test(msg)) {
      const m = msg.match(/(\d+)\s+owned/i);
      if (m) ownedScored = Number(m[1]);
    }
    if (code === 'TENURE_DISCOUNT' || /non-owned|leased/i.test(msg)) {
      const m = msg.match(/(\d+)\s+(?:non-owned|leased)/i);
      if (m) leasedDown = Number(m[1]);
    }
  }

  if (!total && (ownedScored || leasedDown)) {
    total = Math.max(ownedScored + leasedDown, list.length);
  }

  return { total, ownedScored, leasedDown };
}

function dataNotesFromView(opts: {
  reasonCodes: ReasonCode[];
  warnings?: string[];
  farms?: FarmAssessment[];
  nFailed?: number | null;
}): string[] {
  const notes: string[] = [];
  const skipCodes = new Set(['PORTFOLIO_SIZE', 'TENURE_DISCOUNT']);

  for (const r of opts.reasonCodes || []) {
    const code = String(r?.code || '');
    if (skipCodes.has(code)) continue;
    const msg = r?.message ? String(r.message) : code;
    if (msg) notes.push(msg);
  }

  for (const w of opts.warnings || []) {
    if (w) notes.push(String(w));
  }

  for (const f of opts.farms || []) {
    if (f.skipped_reason) {
      const id = f.farm_id || f.plot_key || 'plot';
      notes.push(`${id}: ${String(f.skipped_reason).replace(/^error:/, 'Error: ')}`);
    }
  }

  if (opts.nFailed && opts.nFailed > 0) {
    notes.push(`${opts.nFailed} plot(s) failed during scoring.`);
  }

  return Array.from(new Set(notes.map((n) => n.trim()).filter(Boolean)));
}

/**
 * Single KBS block beside Farmer Details (60% / 40%).
 * HARD RULE: only pass `data` when job SUCCESS.
 */
export function RiskScoreCard({
  data,
  placeholder,
  statusMessage,
}: {
  data: AssessmentPayload | null;
  placeholder?: boolean;
  statusMessage?: string;
}) {
  const showPlaceholder = placeholder || !data;
  const view = useRiskView(data);
  const insufficient = !!view.insufficientData || view.score == null;
  const kbs = showPlaceholder || insufficient ? null : toKbsScore(view.score);
  const band = kbsBandForScore(kbs);

  const weights = resolveWeights(view.weights);
  const pillarKeys = SUBSTANTIVE_SUBINDEX_KEYS.filter(
    (k) => view.subIndices[k] != null || weights[k] != null
  );

  const holding = plotHoldingStats(view.perFarm, view.nPlotsTotal, view.reasonCodes);
  const notes = showPlaceholder
    ? []
    : dataNotesFromView({
        reasonCodes: view.reasonCodes,
        warnings: view.warnings,
        farms: view.perFarm,
        nFailed: view.nPlotsFailed,
      });
  const riskSurface = bandCardSurface(band);

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-3.5 sm:p-4 h-full flex flex-col">
      <h2 className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider mb-0.5 bg-emerald-50/80 inline-block px-1.5 py-0.5 rounded">
        Krishi Bhoomi Score (KBS)
      </h2>
      <p className="text-[11px] text-stone-500 mb-2.5 leading-snug">
        Field-health index, scored 300–900. Reflects land and crop condition only — not a credit
        score, loan amount, or default probability.
      </p>

      {showPlaceholder ? (
        <div className="grid grid-cols-[70%_1fr] gap-2.5 items-stretch flex-1">
          <div className="opacity-70 min-w-0">
            <KbsGauge score={null} band={null} placeholder compact />
          </div>
          <div className="rounded-lg border border-[#E4DFD4] bg-[#F5F2EB]/60 p-3 flex flex-col justify-center">
            <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-wider mb-1">
              Overall risk
            </p>
            <p className="text-xs text-stone-500 leading-snug">
              {statusMessage || 'Analyzing plots…'}
            </p>
          </div>
        </div>
      ) : (
        <>
          {insufficient && (
            <div className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-950">
              Insufficient data to score this farmer
              {view.category === 'INSUFFICIENT_DATA' ? ' (no scorable plots).' : '.'}
            </div>
          )}

          {/* Row: gauge 70% + Overall Risk card 30% (card hugs content height) */}
          <div className="grid grid-cols-1 sm:grid-cols-[70%_1fr] gap-2.5 items-center">
            <div className="min-w-0">
              <KbsGauge score={kbs} band={band} compact />
            </div>
            <div
              className="relative overflow-hidden rounded-xl border-2 p-3.5 flex flex-col gap-2.5 self-center w-full"
              style={{
                background: riskSurface.background,
                borderColor: riskSurface.border,
              }}
            >
              <div
                className="absolute top-0 left-0 right-0 h-1"
                style={{ background: riskSurface.accent }}
                aria-hidden
              />
              <p
                className="text-[10px] font-bold uppercase tracking-wider"
                style={{ color: riskSurface.ink }}
              >
                Overall risk
              </p>
              {band ? (
                <>
                  <span
                    className="inline-flex self-start items-center px-2.5 py-1 rounded-full text-xs font-bold border shadow-sm"
                    style={bandChipStyle(band)}
                  >
                    {band.riskLabel} risk
                  </span>
                  {kbs != null && (
                    <div className="pt-0.5">
                      <p
                        className="text-2xl font-bold font-mono tabular-nums leading-none tracking-tight"
                        style={{ color: riskSurface.ink }}
                      >
                        {kbs}
                        <span className="text-sm font-semibold text-stone-500 ml-1">
                          / {KBS_MAX}
                        </span>
                      </p>
                      <p className="text-[10px] font-semibold uppercase tracking-wider text-stone-500 mt-1">
                        KBS · {band.name}
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <p className="text-xs text-stone-500">No band</p>
              )}
            </div>
          </div>

          {/* Full-width summary strip */}
          <div className="mt-2.5 rounded-lg border border-[#E4DFD4] bg-[#F5F2EB]/40 px-3 py-2.5 space-y-1">
            {band ? (
              <p className="text-xs text-stone-700 leading-snug">
                Score sits in the <span className="font-semibold">{band.name}</span> band —{' '}
                {band.shortDescription}
              </p>
            ) : (
              <p className="text-xs text-stone-500">No KBS band available for this run.</p>
            )}
            {holding.total > 0 && (
              <p className="text-[11px] text-stone-600">
                {holding.total} plots on holding · {holding.ownedScored} owned scored ·{' '}
                {holding.leasedDown} down-weighted
              </p>
            )}
          </div>

          {/* Compact pillar cards */}
          {pillarKeys.length > 0 && (
            <div className="mt-2.5 grid grid-cols-2 lg:grid-cols-4 gap-1.5">
              {pillarKeys.map((k) => {
                const v = view.subIndices[k] ?? 0;
                const w = weights[k];
                const pct = Math.max(0, Math.min(100, v));
                const bar = scoreColor(pct);
                return (
                  <div
                    key={k}
                    className="rounded-md border border-[#E4DFD4] bg-[#F5F2EB]/40 px-2 py-1.5 space-y-1"
                  >
                    <div className="flex items-start justify-between gap-1">
                      <div className="flex items-center gap-1 min-w-0">
                        <PillarIcon kind={k} />
                        <span className="text-[10px] font-semibold text-stone-700 leading-tight line-clamp-2">
                          {subIndexLabel(k)}
                        </span>
                      </div>
                      <span className="text-xs font-bold font-mono text-stone-900 tabular-nums shrink-0">
                        {formatScoreOne(v)}
                      </span>
                    </div>
                    <div className="h-1 bg-[#E8E4DB] rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${pct}%`, background: bar }}
                      />
                    </div>
                    {w != null && (
                      <p className="text-[10px] text-stone-500 leading-none">
                        weight {formatScoreOne(w)}%
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {notes.length > 0 && (
            <details className="mt-2 rounded-md border border-[#E4DFD4] bg-[#F5F2EB]/30 px-2.5 py-1.5">
              <summary className="cursor-pointer text-[11px] font-semibold text-stone-600 select-none">
                Data notes ({notes.length})
              </summary>
              <ul className="mt-1.5 space-y-1 pb-0.5">
                {notes.map((n, i) => (
                  <li key={i} className="text-[11px] text-stone-600 leading-snug">
                    • {n}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
