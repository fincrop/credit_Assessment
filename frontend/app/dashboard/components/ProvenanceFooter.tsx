'use client';

import type { AssessmentPayload } from '../../types/assessment';

/**
 * Versions, sources and window — collapsed by default.
 *
 * Reader ① (the loan officer, FD-5) never opens this. Reader ② (the credit
 * committee) opens nothing else, and will ask which pipeline version produced
 * a number they are being asked to defend six months from now.
 *
 * Collapsed rather than omitted: provenance that cannot be found is the same
 * as provenance that does not exist, the first time someone audits a decision.
 */

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value == null || value === '') return null;
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-t border-rule first:border-t-0">
      <dt className="text-[11px] text-ink-muted">{label}</dt>
      <dd className="text-[11px] font-mono text-ink-2 text-right break-all">{value}</dd>
    </div>
  );
}

export function ProvenanceFooter({ data }: { data: AssessmentPayload | null }) {
  if (!data) return null;

  const stats = data.continuous_data_stats;
  const sq = data.signal_quality_summary;
  // The holding-level aggregate carries weights and a gate but no version or
  // method of its own — those live on the payload. Reading them off the union
  // would be a type error at best and a blank field at worst.
  const risk = data.risk_assessment ?? data.farmer_level;
  const plotRisk = data.risk_assessment;

  return (
    <details className="rounded-xl border border-rule bg-paper/40 px-4 py-3 group">
      <summary className="cursor-pointer select-none text-[12px] font-semibold text-ink-2 marker:content-['']">
        <span className="group-open:hidden">▸ </span>
        <span className="hidden group-open:inline">▾ </span>
        Method and provenance
      </summary>

      <dl className="mt-3 grid gap-x-6 sm:grid-cols-2">
        <div>
          <Row label="Pipeline version" value={data.pipeline_version} />
          <Row label="Pipeline profile" value={data.pipeline_profile} />
          <Row label="Index version" value={data.index_version ?? plotRisk?.index_version} />
          <Row label="Method" value={data.method ?? plotRisk?.method} />
          <Row label="ML mode" value={data.ml_mode} />
          <Row
            label="Assessed"
            value={data.assessment_date ? String(data.assessment_date).slice(0, 19) : null}
          />
          <Row
            label="Processing time"
            value={
              data.processing_time_seconds != null
                ? `${data.processing_time_seconds.toFixed(1)} s`
                : null
            }
          />
        </div>
        <div>
          <Row
            label="Observation window"
            value={
              stats?.date_range?.start && stats?.date_range?.end
                ? `${stats.date_range.start} → ${stats.date_range.end}`
                : null
            }
          />
          <Row
            label="Observations"
            value={
              stats?.valid_observations != null && stats?.grid_slots != null
                ? `${stats.valid_observations} of ${stats.grid_slots} slots`
                : null
            }
          />
          <Row label="Bin interval" value={stats?.interval_days ? `${stats.interval_days} d` : null} />
          <Row
            label="Signal version"
            value={(sq as Record<string, unknown> | undefined)?.signal_version as string}
          />
          <Row label="Land-cover gate" value={data.land_cover?.gate_version} />
          <Row label="Viability check" value={data.parcel_viability?.version} />
          <Row label="Sufficiency check" value={data.data_sufficiency?.version} />
          <Row label="Geometry source" value={data.geospatial_prep?.geometry_source} />
        </div>
      </dl>

      {risk?.weights && (
        <div className="mt-3 pt-2.5 border-t border-rule">
          <p className="text-[11px] text-ink-muted">
            Driver weights{' '}
            <span className="font-mono text-ink-2">
              {Object.entries(risk.weights)
                .map(([k, v]) => `${k} ${v}%`)
                .join(' · ')}
            </span>
          </p>
          <p className="text-[11px] text-ink-muted mt-1 leading-relaxed">
            Expert-weighted (AHP-style); provisional, pending sign-off. The index is an
            agronomic measure of land and crop condition — it carries no repayment
            calibration and is not a probability of default.
          </p>
        </div>
      )}

      {(data.pipeline_stages?.length ?? 0) > 0 && (
        <p className="text-[10px] text-ink-muted mt-2.5 font-mono leading-relaxed">
          stages: {data.pipeline_stages!.join(' → ')}
        </p>
      )}
    </details>
  );
}
