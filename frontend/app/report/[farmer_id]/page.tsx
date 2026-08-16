'use client';

import { Suspense, use, useMemo } from 'react';
import Link from 'next/link';
import { useReport } from '../../lib/useReport';
import { hasSection } from '../../lib/reportClient';
import { riskViewFromReport, captionsFromReport } from '../../lib/reportView';
import { bandForIndex, NO_BAND } from '../../lib/kbsScore';
import { ScoreWaterfall } from '../../dashboard/components/ScoreWaterfall';
import { NdviTrajectory } from '../../dashboard/components/NdviTrajectory';
import { ObservationCalendar } from '../../dashboard/components/ObservationCalendar';
import { OmittedPanels } from '../../dashboard/components/OmittedPanel';
import { LandCoverPanel } from '../../dashboard/components/LandCoverPanel';
import { CropVerificationPanel } from '../../dashboard/components/CropVerificationPanel';
import { ScoreTrendSparkline } from '../../dashboard/components/ScoreTrendSparkline';
import { FootprintBanner } from '../../dashboard/components/ConfidenceBadge';
import type { ReportPayload } from '../../types/report';

/**
 * Farmer Assessment Report — the dossier.
 *
 * Print-first, one column, driven entirely by the report contract. Replaces
 * `AssessmentPrintReport`, which reformatted the dashboard through @media
 * print rules; the report is a different contract from the dashboard's, and
 * rendering it through the dashboard guaranteed the two would drift.
 *
 * Every section is gated on `sections_present` or on the field itself, and
 * anything the pipeline refuses to produce is rendered as an explicitly empty
 * slot carrying the backend's own reason (see OmittedPanels). A blank space
 * where a panel used to be is an invitation for someone to fill it later.
 *
 * FARMER IDENTITY IS DELIBERATELY ABSENT. The payload never reaches into
 * farm_info for Aadhaar or mobile because the masking policy is unsettled
 * (backend D-5 / frontend FD-6). The header states that rather than leaving a
 * gap, so the omission reads as a decision instead of an oversight.
 */

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
        {label}
      </dt>
      <dd className="text-[12px] text-ink mt-0.5 font-mono">{value ?? '—'}</dd>
    </div>
  );
}

function ScoreHeader({ report }: { report: ReportPayload }) {
  const kbs = report.score?.kbs;
  const band = bandForIndex(report.score?.index_score);

  return (
    <section
      className="rounded-xl border px-5 py-4"
      style={{
        background: band?.surface ?? NO_BAND.surface,
        borderColor: band?.border ?? NO_BAND.border,
      }}
    >
      <p className="text-[10px] font-bold uppercase tracking-widest" style={{ color: band?.ink ?? NO_BAND.ink }}>
        Krishi Bhoomi Score
      </p>
      <div className="flex items-baseline gap-3 flex-wrap mt-1">
        <span
          className="font-mono tabular-nums text-4xl font-bold leading-none"
          style={{ color: band?.ink ?? NO_BAND.ink }}
        >
          {kbs ?? '—'}
        </span>
        <span className="text-[13px] font-semibold text-ink-2">
          of {report.score?.scale_max ?? 900}
          {band ? ` · ${band.name}` : ''}
        </span>
      </div>
      {/* Renders nothing at all on a first assessment — see the component. */}
      <div className="mt-2">
        <ScoreTrendSparkline report={report} />
      </div>
      <p className="text-[11px] text-ink-2 mt-2 leading-relaxed max-w-2xl">
        A field-health index scored {report.score?.scale_min ?? 300}–
        {report.score?.scale_max ?? 900} from satellite observation of this land.
        <strong> It is not a credit score, a loan amount, or a probability of
        default</strong>, and it carries no repayment calibration.
      </p>
    </section>
  );
}

function ReportBody({ report }: { report: ReportPayload }) {
  const view = useMemo(() => riskViewFromReport(report), [report]);
  const captions = useMemo(() => captionsFromReport(report), [report]);
  const refused = report.status !== 'SUCCESS';

  return (
    <div className="space-y-4">
      <FootprintBanner footprint={report.footprint} />

      {hasSection(report, 'score') && !refused ? (
        <ScoreHeader report={report} />
      ) : (
        <section className="rounded-xl border border-rule bg-paper/60 px-5 py-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-accent-gold">
            No score produced
          </p>
          <p className="text-[13px] text-ink mt-1.5 leading-snug max-w-2xl">
            This assessment ended in <span className="font-mono">{report.status}</span>.
            No Krishi Bhoomi Score was produced, and none should be inferred.
          </p>
        </section>
      )}

      {hasSection(report, 'narrative') && (
        <section className="rounded-xl border border-rule bg-card px-5 py-4">
          <h2 className="text-sm font-semibold text-ink">Assessment</h2>
          <p className="text-[13px] text-ink-2 mt-2 leading-relaxed whitespace-pre-line">
            {report.narrative.text}
          </p>
          {report.narrative.source && (
            <p className="text-[10px] text-ink-muted mt-2 font-mono">
              narrative source: {report.narrative.source}
            </p>
          )}
        </section>
      )}

      {hasSection(report, 'sub_indices') && !refused && (
        <ScoreWaterfall view={view} captions={captions} footprint={report.footprint} scopeLabel="parcel" />
      )}

      {hasSection(report, 'ndvi_trajectory') && (
        <NdviTrajectory trajectory={report.ndvi_trajectory} />
      )}

      <ObservationCalendar
        sufficiency={report.data_sufficiency}
        trajectory={report.ndvi_trajectory}
      />

      {hasSection(report, 'land_cover') && <LandCoverPanel landCover={report.land_cover} />}
      {hasSection(report, 'crop_verification') && (
        <CropVerificationPanel verification={report.crop_verification} />
      )}

      {/* Panels this pipeline will not fill, in the backend's own words. */}
      <OmittedPanels omitted={report.omitted} />

      <section className="rounded-xl border border-rule bg-paper/50 px-5 py-4">
        <h2 className="text-sm font-semibold text-ink">Method and provenance</h2>
        <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-3">
          <Field label="Pipeline" value={report.methodology?.pipeline_version} />
          <Field label="Index" value={report.methodology?.index_version} />
          <Field label="Signal" value={report.methodology?.signal_version} />
          <Field label="Imagery" value={report.methodology?.satellite_provider} />
          <Field label="Cloud mask" value={report.methodology?.cloud_mask_version} />
          <Field label="Assessed" value={String(report.assessment_date ?? '').slice(0, 10)} />
        </dl>
        {report.methodology?.weights_note && (
          <p className="text-[11px] text-ink-muted mt-3 leading-relaxed">
            {report.methodology.weights_note}
          </p>
        )}
        <p className="text-[10px] text-ink-muted mt-2 font-mono break-all">
          integrity sha256 · {report.integrity?.content_sha256}
        </p>
        {report.integrity?.note && (
          <p className="text-[10px] text-ink-muted mt-1 leading-snug">{report.integrity.note}</p>
        )}
      </section>
    </div>
  );
}

function ReportContent({ farmerId }: { farmerId: string }) {
  const { report, loading, problem } = useReport(farmerId);

  if (loading) {
    return <p className="text-sm text-ink-muted">Loading report…</p>;
  }

  if (problem) {
    return (
      <div className="rounded-xl border border-rule bg-card px-5 py-6">
        <h1 className="text-base font-semibold text-ink">Report unavailable</h1>
        <p className="text-[13px] text-ink-2 mt-1.5">{problem.message}</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="rounded-xl border border-rule bg-card px-5 py-6">
        <h1 className="text-base font-semibold text-ink">No report for this farmer</h1>
        <p className="text-[13px] text-ink-2 mt-1.5 max-w-xl leading-relaxed">
          No assessment has been stored for{' '}
          <span className="font-mono">{farmerId}</span>, or the report service is
          not configured for this deployment. Run an assessment first — the report
          is read-only and never triggers one, so it can never show a different
          number from the assessment it describes.
        </p>
        <Link
          href={`/dashboard?farmer_id=${encodeURIComponent(farmerId)}`}
          className="inline-flex mt-4 bg-accent hover:opacity-90 text-white font-semibold px-4 py-2 rounded-lg text-sm transition-opacity"
        >
          Go to assessment
        </Link>
      </div>
    );
  }

  return (
    <>
      <header className="report-header">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-lg font-bold text-ink tracking-tight">
              Farmer Assessment Report
            </h1>
            <p className="text-[11px] text-ink-muted font-mono mt-0.5">
              {report.report_id} · farmer {report.farmer_id}
            </p>
          </div>
          <div className="flex items-center gap-2 no-print">
            <Link
              href={`/dashboard?farmer_id=${encodeURIComponent(farmerId)}`}
              className="text-[12px] font-semibold text-ink-2 border border-rule rounded-md px-2.5 py-1.5 hover:bg-paper transition-colors"
            >
              Dashboard
            </Link>
            <button
              type="button"
              onClick={() => window.print()}
              className="text-[12px] font-semibold bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 transition-opacity"
            >
              Print / PDF
            </button>
          </div>
        </div>

        {/* The identity slot. Stated, not left blank — an empty space here
            reads as an oversight and invites someone to fill it from
            farm_info without settling the masking policy first. */}
        <p className="text-[11px] text-ink-muted mt-2.5 leading-relaxed max-w-2xl border-t border-rule pt-2.5">
          Farmer identity is not included in this report. The masking policy for
          personal data is not settled, so the assessment service does not emit
          it and this page does not fetch it. Identify the borrower from your own
          records against the farmer id above.
        </p>
      </header>

      <ReportBody report={report} />
    </>
  );
}

export default function ReportPage({
  params,
}: {
  params: Promise<{ farmer_id: string }>;
}) {
  const { farmer_id } = use(params);
  const farmerId = decodeURIComponent(farmer_id);

  return (
    <main className="min-h-screen bg-paper py-6 px-4 print:p-0 print:bg-white">
      <div className="report-sheet max-w-3xl mx-auto space-y-4">
        <Suspense fallback={<p className="text-sm text-ink-muted">Loading report…</p>}>
          <ReportContent farmerId={farmerId} />
        </Suspense>
      </div>
    </main>
  );
}
