'use client';

/**
 * Panels this pipeline will not fill, rendered as explicitly empty.
 *
 * The report payload ships an `omitted{}` block naming things the supplied
 * design has that nothing real stands behind — a policy recommendation, a
 * district-median comparison line, a reviewer field. The backend's own comment
 * on the subject is worth repeating: filling them is worse than omitting them,
 * because a number in a report gets used.
 *
 * We do NOT silently leave a gap where they were. A blank slot in a layout is
 * an invitation for someone to fill it later, and by then the reason will have
 * been forgotten. The refusal is stated, in the backend's words, in the place
 * the panel would have been.
 */

const TITLES: Record<string, string> = {
  policy_action: 'Suggested action',
  peer_comparison: 'Peer comparison',
  review_workflow: 'Review status',
};

function title(key: string): string {
  return (
    TITLES[key] ??
    key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
  );
}

export function OmittedPanel({ panelKey, reason }: { panelKey: string; reason: string }) {
  return (
    <section
      className="rounded-xl border border-dashed border-rule bg-paper/40 px-4 py-3.5"
      aria-label={`${title(panelKey)} — not produced`}
    >
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h3 className="text-[13px] font-semibold text-ink-2">{title(panelKey)}</h3>
        <span className="text-[10px] font-bold uppercase tracking-widest text-ink-muted">
          Not produced
        </span>
      </div>
      <p className="text-[12px] text-ink-muted mt-1.5 leading-relaxed max-w-2xl">{reason}</p>
    </section>
  );
}

/**
 * Renders every entry in an `omitted{}` map.
 *
 * `cold` marks the ones that become real with volume (peer cohorts) rather
 * than the ones we structurally do not do (policy engine, reviewer). A reader
 * deciding whether to wait for a feature needs that distinction.
 */
export function OmittedPanels({
  omitted,
  only,
}: {
  omitted: Record<string, string> | null | undefined;
  only?: string[];
}) {
  if (!omitted) return null;
  const entries = Object.entries(omitted).filter(
    ([k, v]) => typeof v === 'string' && v && (!only || only.includes(k))
  );
  if (entries.length === 0) return null;

  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => (
        <OmittedPanel key={k} panelKey={k} reason={v} />
      ))}
    </div>
  );
}

/**
 * A feature that is pending rather than absent — the cold peer cohort.
 *
 * "6 of 20 parcels in this zone" is a credible progress signal. Hiding the
 * panel entirely makes the feature look like it does not exist; showing an
 * empty chart makes it look broken.
 */
export function ColdStartPanel({
  heading,
  have,
  need,
  explanation,
}: {
  heading: string;
  have: number | null | undefined;
  need: number;
  explanation: string;
}) {
  const n = typeof have === 'number' && Number.isFinite(have) ? have : null;
  const frac = n != null ? Math.max(0, Math.min(1, n / need)) : 0;

  return (
    <section className="rounded-xl border border-rule bg-card px-4 py-3.5" aria-label={heading}>
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h3 className="text-[13px] font-semibold text-ink-2">{heading}</h3>
        <span className="text-[11px] font-mono tabular-nums text-ink-muted">
          {n != null ? `${n} of ${need}` : `needs ${need}`}
        </span>
      </div>

      {/* Progress, not a data bar — it encodes how close the feature is to
          existing, which is exactly what the reader wants to know. */}
      <div className="h-1.5 bg-rule-strong rounded-full overflow-hidden mt-2.5">
        <div
          className="h-full rounded-full bg-accent-gold"
          style={{ width: `${frac * 100}%` }}
        />
      </div>

      <p className="text-[12px] text-ink-muted mt-2 leading-relaxed max-w-2xl">{explanation}</p>
    </section>
  );
}
