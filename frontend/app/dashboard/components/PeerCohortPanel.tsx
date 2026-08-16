'use client';

import type { PerformanceAnalysis } from '../../types/assessment';
import { ChartFrame } from './ChartFrame';

/**
 * Peer comparison — and why there isn't one yet.
 *
 * FD-7 chose to render this rather than hide it: "pending" and "absent" look
 * identical when a panel is missing, and a lender evaluating the product
 * should be able to see that peer-relativity is coming rather than assume it
 * was never built.
 *
 * WHAT THIS PANEL CANNOT SAY, AND WHY
 * ───────────────────────────────────
 * There is no distribution strip and no "you rank Nth" here, because the data
 * for that sentence does not exist. `peer_benchmarking` on an assessment
 * carries only `cohort_key`, whether a peer engine ran, and how many cycles
 * were scored peer-relatively. The cohort's SIZE and the parcel's POSITION in
 * it live in a `cohort_stats` collection written by a separate job, and are
 * not on the assessment at all.
 *
 * So the honest progress signal is "this zone's cohort has not warmed", not
 * "6 of 20" — a count I would have to invent. When the cohort job exposes its
 * counts on the assessment, this panel gains a real progress bar; until then
 * it states the condition rather than faking the measurement.
 */

export function PeerCohortPanel({
  performance,
}: {
  performance: PerformanceAnalysis | null | undefined;
}) {
  const pb = performance?.peer_benchmarking;
  const peerScored = pb?.n_cycles_peer_scored ?? 0;
  const engineRan = !!pb?.engine;
  const warm = peerScored > 0;

  return (
    <ChartFrame
      title="Comparison with similar farms"
      subtitle="How this parcel performs against others in the same agro-climatic zone."
      empty={
        warm ? undefined : (
          <>
            <strong className="text-ink-2">Not yet available for this zone.</strong>{' '}
            Peer comparison needs a warm cohort — enough assessed parcels in the same
            agro-climatic zone for a median to mean anything. No zone has reached that
            threshold yet, so vigour is scored against an absolute reference rather
            than against neighbours.
            <br />
            <br />
            This is a volume problem and it resolves on its own as assessments
            accumulate. Nothing on this page is estimated from a partial cohort:
            a median built from four farms would be worse than no median at all.
            {pb?.cohort_key && (
              <>
                <br />
                <br />
                <span className="font-mono text-[11px] text-ink-muted">
                  zone: {pb.cohort_key}
                  {engineRan ? ` · engine: ${pb.engine}` : ' · engine: not run'}
                </span>
              </>
            )}
          </>
        )
      }
    >
      <div className="rounded-lg border border-rule bg-paper/50 px-3.5 py-3">
        <p className="text-[13px] text-ink leading-snug">
          <span className="font-semibold">{peerScored}</span> growth{' '}
          {peerScored === 1 ? 'cycle was' : 'cycles were'} scored against peers in this
          zone rather than against an absolute reference.
        </p>
        {pb?.cohort_key && (
          <p className="text-[11px] text-ink-muted mt-1.5 font-mono">
            zone: {pb.cohort_key}
            {engineRan && ` · engine: ${pb.engine}`}
          </p>
        )}
        <p className="text-[11px] text-ink-muted mt-2 leading-relaxed">
          The parcel&apos;s rank within the cohort is not shown: cohort membership is
          maintained separately from the assessment, so a position quoted here would
          not be one this run measured.
        </p>
      </div>
    </ChartFrame>
  );
}
