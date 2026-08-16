'use client';

import type { RiskView } from '../../lib/useRiskView';
import type { Footprint } from '../../types/assessment';
import { formatScoreOne, resolveWeights, subIndexLabel, SUBSTANTIVE_SUBINDEX_KEYS } from '../../lib/formatRisk';
import { bandForIndex, scoreColor, toKbsScore, KBS_MAX, NO_BAND } from '../../lib/kbsScore';
import { CHROME } from '../../lib/vizPalette';
import { ChartFrame, type TableColumn } from './ChartFrame';

/**
 * How the score was actually built.
 *
 * The pipeline's arithmetic, verified against risk_index_engine.py:
 *
 *   additive  = Σ (score_i × weight_i / 100)   over landuse, vigor, stability, weather
 *   raw_index = clip(additive + benefits.bonus)
 *   index     = clip(raw_index × gate)
 *
 * Two things this gets right that the four-separate-bars version could not:
 *
 * ① CONTRIBUTION, NOT SCORE. A sub-index of 42 matters differently at weight
 *   35 than at weight 15. The bar shows the points it contributed, and its
 *   track shows the points it COULD have — so the empty part of each track is
 *   the recoverable loss, which is the number a loan officer actually acts on.
 *
 * ② THE GATE IS A STEP, NOT A FOOTNOTE. It was a lone "0.92" in a box that
 *   nobody could interpret. Here it is the last operation, in points.
 *
 * A note on the footprint penalty: when the boundary was substituted the
 * engine multiplies the gate by 0.85 IN PLACE. It is not a separate term, so
 * it is not drawn as a separate step — it is named inside the gate row.
 * Splitting it would misstate the formula.
 */

interface Row {
  key: string;
  label: string;
  score: number;
  weight: number;
  contribution: number;
  headroom: number;
  isWeakest: boolean;
  caption?: string;
}

const TABLE_COLUMNS: TableColumn<Row>[] = [
  { header: 'Driver', cell: (r) => r.label },
  { header: 'Score', numeric: true, cell: (r) => formatScoreOne(r.score) },
  { header: 'Weight', numeric: true, cell: (r) => `${formatScoreOne(r.weight)}%` },
  { header: 'Contributed', numeric: true, cell: (r) => `+${r.contribution.toFixed(1)}` },
  { header: 'Headroom', numeric: true, cell: (r) => r.headroom.toFixed(1) },
];

function StepRow({
  label,
  detail,
  value,
  emphasis,
  tone,
}: {
  label: string;
  detail?: React.ReactNode;
  value: string;
  emphasis?: boolean;
  tone?: string;
}) {
  return (
    <div
      className={`flex items-baseline justify-between gap-3 py-1.5 ${
        emphasis ? 'border-t border-rule mt-1 pt-2' : ''
      }`}
    >
      <div className="min-w-0">
        <span
          className={`text-[12px] ${emphasis ? 'font-semibold text-ink' : 'text-ink-2'}`}
        >
          {label}
        </span>
        {detail && <span className="text-[11px] text-ink-muted ml-2">{detail}</span>}
      </div>
      <span
        className={`font-mono tabular-nums shrink-0 ${
          emphasis ? 'text-[15px] font-bold' : 'text-[13px]'
        }`}
        style={{ color: tone ?? (emphasis ? undefined : CHROME.labelStrong) }}
      >
        {value}
      </span>
    </div>
  );
}

export function ScoreWaterfall({
  view,
  captions,
  footprint,
  scopeLabel = 'holding',
}: {
  view: RiskView;
  captions?: Record<string, string>;
  footprint?: Footprint | null;
  scopeLabel?: string;
}) {
  const weights = resolveWeights(view.weights);

  const rows: Row[] = SUBSTANTIVE_SUBINDEX_KEYS.filter(
    (k) => view.subIndices[k] != null && weights[k] != null
  ).map((k) => {
    const score = Math.max(0, Math.min(100, view.subIndices[k]!));
    const weight = weights[k]!;
    const contribution = (score * weight) / 100;
    return {
      key: k,
      label: subIndexLabel(k),
      score,
      weight,
      contribution,
      headroom: weight - contribution,
      isWeakest: view.weakSubIndices.includes(k),
      caption: captions?.[k],
    };
  });

  if (rows.length === 0 || view.score == null) {
    return (
      <ChartFrame
        title="How this score was built"
        empty="Sub-index scores are not available for this assessment, so the build-up cannot be shown."
      />
    );
  }

  const additive = rows.reduce((a, r) => a + r.contribution, 0);
  const bonus = view.benefits.bonus ?? 0;
  const raw = view.rawIndex ?? additive + bonus;
  const gate = view.gate;
  const index = view.score;
  const kbs = toKbsScore(index);
  const band = bandForIndex(index);
  const gateLoss = gate != null ? raw - index : 0;

  // The engine clips raw_index to 0–100, so the parts need not sum to it.
  // Showing the parts adding to a different total than the stated raw index
  // would look like an arithmetic error; naming the clip is the honest fix.
  const clipped = Math.abs(additive + bonus - raw) > 0.15;

  // Track width is the weight, so all four tracks together span the full
  // 100 points a raw index can reach. Fill is the contribution.
  const totalWeight = rows.reduce((a, r) => a + r.weight, 0) || 100;

  return (
    <ChartFrame
      title="How this score was built"
      subtitle={`Each driver contributes its score times its weight. The unfilled part of a track is the points still available on this ${scopeLabel}.`}
      tableRows={rows}
      tableColumns={TABLE_COLUMNS}
      note={
        <>
          Weights are expert-set (AHP-style) and provisional, pending sign-off.
          Contributions are shown in index points, not KBS points — the 0–100
          index is mapped to {KBS_MAX === 900 ? '300–900' : 'the KBS scale'} only at
          the end.
        </>
      }
    >
      {/* Composite bar — the whole raw index in one line, so the reader sees
          the shape of the holding before reading any number. */}
      <div className="flex gap-[2px] h-7 rounded-md overflow-hidden mb-4" aria-hidden>
        {rows.map((r) => (
          <div
            key={r.key}
            className="relative"
            style={{ width: `${(r.weight / totalWeight) * 100}%`, background: CHROME.grid }}
            title={`${r.label}: ${r.contribution.toFixed(1)} of ${r.weight.toFixed(1)} points`}
          >
            <div
              className="absolute inset-y-0 left-0"
              style={{ width: `${r.score}%`, background: scoreColor(r.score) }}
            />
          </div>
        ))}
      </div>

      <div className="space-y-2.5">
        {rows.map((r) => (
          <div key={r.key}>
            <div className="flex items-baseline justify-between gap-2 mb-1">
              <span className="text-[12px] text-ink-2 min-w-0 truncate">
                {r.label}
                <span className="text-ink-muted ml-1.5 font-mono text-[11px]">
                  {formatScoreOne(r.score)} × {formatScoreOne(r.weight)}%
                </span>
                {r.isWeakest && (
                  <span
                    className="ml-1.5 text-[10px] font-bold uppercase tracking-wide"
                    style={{ color: '#7A5405' }}
                  >
                    ◀ weakest
                  </span>
                )}
              </span>
              <span className="font-mono tabular-nums text-[12px] text-ink shrink-0">
                +{r.contribution.toFixed(1)}
              </span>
            </div>
            {/* Flat fill — a gradient along a bar makes the same length read as
                a different number depending which end you scan from. */}
            <div
              className="h-2 rounded-sm overflow-hidden"
              style={{ width: `${(r.weight / totalWeight) * 100}%`, background: CHROME.grid }}
            >
              <div
                className="h-full"
                style={{ width: `${r.score}%`, background: scoreColor(r.score) }}
              />
            </div>
            {r.caption && (
              <p className="text-[11px] text-ink-muted mt-1 leading-snug">{r.caption}</p>
            )}
          </div>
        ))}
      </div>

      <div className="mt-3">
        {bonus > 0 && (
          <StepRow
            label="Government benefits"
            detail="positive-only; unknown never penalises"
            value={`+${bonus.toFixed(1)}`}
          />
        )}
        {clipped && (
          <StepRow
            label="Clipped to the 0–100 range"
            value={(raw - additive - bonus).toFixed(1)}
          />
        )}
        <StepRow label="Raw index" value={raw.toFixed(1)} emphasis />

        {gate != null && gate < 0.995 ? (
          <StepRow
            label="Confidence gate"
            detail={
              <>
                ×{gate.toFixed(3)}
                {footprint?.geometry_substituted && ' — includes a substituted-footprint discount'}
              </>
            }
            value={`−${gateLoss.toFixed(1)}`}
            tone="#9A2E1F"
          />
        ) : (
          <StepRow label="Confidence gate" detail="no reduction applied" value="0.0" />
        )}

        <StepRow
          label="Index"
          value={index.toFixed(1)}
          emphasis
          tone={band?.ink ?? NO_BAND.ink}
        />
      </div>

      <div
        className="mt-3 rounded-lg border px-3.5 py-2.5 flex items-baseline justify-between gap-3 flex-wrap"
        style={{ background: band?.surface ?? NO_BAND.surface, borderColor: band?.border ?? NO_BAND.border }}
      >
        <span className="text-[12px] font-semibold" style={{ color: band?.ink ?? NO_BAND.ink }}>
          Krishi Bhoomi Score
        </span>
        <span
          className="font-mono tabular-nums text-xl font-bold"
          style={{ color: band?.ink ?? NO_BAND.ink }}
        >
          {kbs ?? '—'}
          <span className="text-[12px] font-semibold ml-1.5">
            / {KBS_MAX}
            {band ? ` · ${band.name}` : ''}
          </span>
        </span>
      </div>
    </ChartFrame>
  );
}
