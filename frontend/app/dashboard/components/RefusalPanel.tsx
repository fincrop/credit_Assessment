'use client';

import type { AssessmentPayload } from '../../types/assessment';
import {
  terminalStateOf,
  landCoverLabel,
  type TerminalVerdict,
} from '../../lib/terminalState';
import { formatNumber } from '../../lib/format';
import { areaMismatchOf, areaMismatchHeadline, areaMismatchSkipReason, formatHa } from '../../lib/areaMismatch';
import { MONITORING_MIN_HA } from '../../lib/plotSkipMessage';

/**
 * The screens for when we did NOT produce a score.
 *
 * These are not error states dressed in amber. A refusal is a finding, and it
 * carries its own evidence — which classes we observed, which weeks we could
 * see, how small the parcel is against the measurable floor.
 *
 * HARD RULE: no gauge, no band arc, no greyed-out score placeholder. An empty
 * dial next to a refusal invites the reader to imagine a number where there is
 * none, and someone will eventually read that empty dial as "zero".
 */

function Figure({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-rule bg-paper/60 px-3 py-2.5">
      <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wider">
        {label}
      </p>
      <p className="text-lg font-mono tabular-nums text-ink mt-1 leading-none">{value}</p>
      {hint && <p className="text-[11px] text-ink-muted mt-1.5 leading-snug">{hint}</p>}
    </div>
  );
}

function Shell({
  eyebrow,
  headline,
  children,
}: {
  eyebrow: string;
  headline: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className="bg-card rounded-xl border border-rule p-5 sm:p-6"
      aria-label={eyebrow}
    >
      <p className="text-[11px] font-bold uppercase tracking-widest text-accent-gold">
        {eyebrow}
      </p>
      <h2 className="text-xl font-semibold text-ink mt-1.5 leading-snug max-w-2xl">
        {headline}
      </h2>
      {children}
    </section>
  );
}

/** Percentage from a 0–1 fraction, or an em dash. Never invents a 0. */
function pct(frac: number | null | undefined): string {
  return typeof frac === 'number' && Number.isFinite(frac)
    ? `${Math.round(frac * 100)}%`
    : '—';
}

function NotFarmland({ verdict }: { verdict: TerminalVerdict }) {
  const lc = verdict.landCover;
  const cls = landCoverLabel(lc?.class);

  return (
    <Shell
      eyebrow="Not scored · not farmland"
      headline={`This parcel was classified as ${cls.toLowerCase()}, so it was not scored as farmland.`}
    >
      {verdict.reason && (
        <p className="text-sm text-ink-2 mt-3 leading-relaxed max-w-2xl">
          {verdict.reason}
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 mt-4">
        <Figure label="Observed class" value={cls} />
        <Figure
          label="Confidence"
          value={
            typeof lc?.confidence === 'number' ? formatNumber(lc.confidence, 2) : '—'
          }
          hint="How strongly the evidence supports this class."
        />
        <Figure
          label="Cultivable"
          value={lc?.is_cultivable === true ? 'Yes' : lc?.is_cultivable === false ? 'No' : '—'}
        />
      </div>

      <p className="text-[12px] text-ink-muted mt-4 leading-relaxed max-w-2xl">
        Nothing failed here. The pipeline declines to produce a credit signal for
        land that is not being farmed — a score computed over a pond or a rooftop
        would look like any other number once it reached a lending decision.
      </p>
    </Shell>
  );
}

function Unobserved({ verdict }: { verdict: TerminalVerdict }) {
  const ev = verdict.dataSufficiency?.evidence;
  const pv = verdict.parcelViability;
  const pvEv = pv?.evidence;
  const mismatch = areaMismatchOf({ viability: pv });
  const viabilityDrivenBy = pv?.outcome === 'not_viable';
  const areaMismatchOnly = Boolean(mismatch || pvEv?.areas_disagree);

  if (viabilityDrivenBy) {
    return (
      <Shell
        eyebrow="Not scored · monitoring area too small"
        headline="This plot is below our minimum monitoring area for scoring."
      >
        {verdict.reason && (
          <p className="text-sm text-ink-2 mt-3 leading-relaxed max-w-2xl">
            {verdict.reason}
          </p>
        )}
        <p className="text-sm font-medium text-ink mt-2 leading-relaxed max-w-2xl">
          Skipped — below our {formatHa(MONITORING_MIN_HA, 2)} minimum ({pvEv?.thresholds?.min_pixels_hard ?? 15}{' '}
          Sentinel-2 pixels at 10 m). Larger mismatched plots may still score with a flag.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mt-4">
          <Figure
            label="Monitoring area"
            value={
              pvEv?.effective_ha != null ? `${formatNumber(pvEv.effective_ha, 2)} ha` : '—'
            }
            hint="Polygon footprint we measure over"
          />
          <Figure
            label="Approx. pixels"
            value={pvEv?.approx_pixels ?? '—'}
            hint={
              pvEv?.thresholds?.min_pixels_hard != null
                ? `Need at least ${pvEv.thresholds.min_pixels_hard}.`
                : undefined
            }
          />
          <Figure
            label="AgriStack area"
            value={
              pvEv?.registered_ha != null ? `${formatNumber(pvEv.registered_ha, 2)} ha` : '—'
            }
          />
          <Figure
            label="Mapped boundary"
            value={
              pvEv?.geometry_ha != null ? `${formatNumber(pvEv.geometry_ha, 2)} ha` : '—'
            }
            hint={pvEv?.areas_disagree ? 'Differs from land record — flagged separately.' : undefined}
          />
        </div>
      </Shell>
    );
  }

  if (areaMismatchOnly && mismatch) {
    return (
      <Shell
        eyebrow="Not scored · area mismatch"
        headline="The AgriStack area does not match the mapped farm boundary."
      >
        <p className="text-sm text-ink-2 mt-3 leading-relaxed max-w-2xl">
          {areaMismatchHeadline(mismatch)}
        </p>
        <p className="text-sm font-medium text-ink mt-2 leading-relaxed max-w-2xl">
          {areaMismatchSkipReason(mismatch)}
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 mt-4">
          <Figure
            label="AgriStack area"
            value={formatHa(mismatch.registeredHa)}
            hint="Given on the land record"
          />
          <Figure
            label="Mapped boundary"
            value={formatHa(mismatch.measuredHa)}
            hint="Calculated from the farm outline"
          />
          <Figure
            label="Difference"
            value={`${mismatch.registeredHa > 0 ? Math.round(Math.abs(1 - mismatch.ratio) * 100) : '—'}%`}
            hint="These should be close if they describe the same plot."
          />
        </div>
        <p className="text-[12px] text-ink-muted mt-4 leading-relaxed max-w-2xl">
          Fix the pairing in AgriStack or re-draw the boundary so the outline matches the
          recorded area, then assess again.
        </p>
      </Shell>
    );
  }

  return (
    <Shell
      eyebrow={viabilityDrivenBy ? 'Not scored · plot too small' : 'Not scored · insufficient observation'}
      headline={
        viabilityDrivenBy
          ? 'The mapped plot is too small to score honestly.'
          : 'We could not see this parcel well enough to score it.'
      }
    >
      {verdict.reason && (
        <p className="text-sm text-ink-2 mt-3 leading-relaxed max-w-2xl">
          {verdict.reason}
        </p>
      )}

      {viabilityDrivenBy ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mt-4">
          <Figure
            label="Usable area"
            value={
              pvEv?.effective_ha != null ? `${formatNumber(pvEv.effective_ha, 2)} ha` : '—'
            }
          />
          <Figure
            label="Approx. pixels"
            value={pvEv?.approx_pixels ?? '—'}
            hint={
              pvEv?.thresholds?.min_pixels_hard != null
                ? `Floor is ${pvEv.thresholds.min_pixels_hard}.`
                : undefined
            }
          />
          <Figure
            label="Registered"
            value={
              pvEv?.registered_ha != null ? `${formatNumber(pvEv.registered_ha, 2)} ha` : '—'
            }
          />
          <Figure
            label="From boundary"
            value={
              pvEv?.geometry_ha != null ? `${formatNumber(pvEv.geometry_ha, 2)} ha` : '—'
            }
            hint={pvEv?.areas_disagree ? 'These disagree materially.' : undefined}
          />
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mt-4">
          <Figure
            label="Weeks observed"
            value={
              ev?.n_observed_bins != null && ev?.n_bins != null
                ? `${ev.n_observed_bins} / ${ev.n_bins}`
                : '—'
            }
            hint={
              ev?.thresholds?.min_observed_fraction != null
                ? `Need ${pct(ev.thresholds.min_observed_fraction)}.`
                : undefined
            }
          />
          <Figure label="Clear coverage" value={pct(ev?.observed_fraction)} />
          <Figure
            label="Longest blind gap"
            value={ev?.largest_blind_gap_days != null ? `${ev.largest_blind_gap_days} d` : '—'}
            hint={
              ev?.thresholds?.blind_gap_days != null
                ? `Limit is ${ev.thresholds.blind_gap_days} d.`
                : undefined
            }
          />
          <Figure
            label="Radar-only weeks"
            value={ev?.n_sar_only_bins ?? '—'}
            hint="Measured, but at lower confidence than optical."
          />
        </div>
      )}

      <p className="text-sm text-ink-2 mt-4 leading-relaxed max-w-2xl font-medium">
        This is a statement about our view of the field, not about the field.
      </p>
      <p className="text-[12px] text-ink-muted mt-1.5 leading-relaxed max-w-2xl">
        {viabilityDrivenBy
          ? 'A parcel this small cannot be measured honestly at 10 m resolution — most of what a satellite sees inside the boundary is the neighbouring field. Re-drawing the boundary is the fix, not re-running the assessment.'
          : 'Cloud cover, a short history, or a run of missing scenes can all produce this. It usually clears with a later observation window; nothing about the parcel needs to change.'}
      </p>
    </Shell>
  );
}

function Failed({ verdict }: { verdict: TerminalVerdict }) {
  return (
    <Shell
      eyebrow="Not scored · pipeline error"
      headline="The assessment did not complete."
    >
      <p className="text-sm text-ink-2 mt-3 leading-relaxed max-w-2xl">
        Something broke on our side. This is not a finding about the parcel, and
        re-running is usually the right response.
      </p>
      {verdict.reason && (
        <pre className="mt-3 text-[11px] font-mono text-ink-2 bg-paper border border-rule rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap">
          {verdict.reason}
        </pre>
      )}
    </Shell>
  );
}

/**
 * Renders the refusal screen, or `null` when there is a score.
 * Callers render this INSTEAD OF the score block, never beside it.
 *
 * Takes either a full payload or a pre-resolved verdict — a multi-farm result
 * carries per-plot refusals as slim records, which `terminalStateOfFarm`
 * resolves without a payload to hand.
 */
export function RefusalPanel({
  data,
  verdict: verdictProp,
}: {
  data?: AssessmentPayload | null;
  verdict?: TerminalVerdict;
}) {
  const verdict = verdictProp ?? terminalStateOf(data);

  switch (verdict.state) {
    case 'NOT_FARMLAND':
      return <NotFarmland verdict={verdict} />;
    case 'UNOBSERVED':
      return <Unobserved verdict={verdict} />;
    case 'FAILED':
      return <Failed verdict={verdict} />;
    default:
      return null;
  }
}
