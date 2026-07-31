'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { useRiskView } from '../../lib/useRiskView';
import {
  formatScoreWhole,
  formatScoreOne,
  formatGateMultiplier,
  polarityIcon,
  subIndexLabel,
  triStateLabel,
  yieldBasisLabel,
} from '../../lib/formatRisk';
import { SubIndexBars } from './SubIndexBars';
import { formatNumber } from '../../lib/format';

/** Screen-hidden; shown only via @media print (same document). */
export function AssessmentPrintReport({ data }: { data: AssessmentPayload }) {
  const view = useRiskView(data);
  const loc = data.location;
  const cycles = data.crop_cycles?.cycles ?? [];
  const seasons = data.performance_analysis?.seasonal_performance ?? [];
  const narrative = data.ai_enrichment?.english_narrative ?? view.narrative;

  return (
    <div className="print-only assessment-print-report" aria-hidden>
      <header className="print-section">
        <h1>Agronomic Risk Index Report</h1>
        <p className="muted">
          Expert-weighted field-health index — not a loan amount, credit limit, or probability of
          default. Weights are provisional.
        </p>
      </header>

      <section className="print-section">
        <h2>Identity</h2>
        <p>
          Farmer <strong className="mono">{data.farmer_id ?? '—'}</strong>
          {loc?.region ? <> · {String(loc.region)}</> : null}
          {data.field_area_ha != null ? <> · {formatNumber(data.field_area_ha, 2)} ha</> : null}
        </p>
        {loc?.latitude != null && loc?.longitude != null && (
          <p className="mono muted">
            {formatNumber(loc.latitude, 4)}°N, {formatNumber(loc.longitude, 4)}°E
          </p>
        )}
        <p>
          PM-KISAN: {triStateLabel(view.benefits.pm_kisan)} · Crop insurance:{' '}
          {triStateLabel(view.benefits.has_crop_insurance)}
        </p>
        {data.assessment_date && (
          <p className="muted">
            Assessed{' '}
            {new Date(data.assessment_date).toLocaleString('en-IN', {
              dateStyle: 'medium',
              timeStyle: 'short',
            })}
          </p>
        )}
      </section>

      <section className="print-section">
        <h2>Index</h2>
        <p className="index-line">
          <strong>{formatScoreWhole(view.score)}</strong>
          {view.category ? <> · {String(view.category)}</> : null}
          {view.indexVersion ? <> · {view.indexVersion}</> : null}
        </p>
        <p className="muted">
          Raw {formatScoreOne(view.rawIndex)} · Gate {formatGateMultiplier(view.gate)}
        </p>
        <SubIndexBars view={view} compact />
      </section>

      <section className="print-section">
        <h2>Reason codes</h2>
        {view.reasonCodes.length === 0 ? (
          <p className="muted">None</p>
        ) : (
          <ul>
            {view.reasonCodes.slice(0, 10).map((rc, i) => (
              <li key={i}>
                {polarityIcon(rc.polarity)}{' '}
                {rc.code ? <span className="mono">{rc.code}</span> : null}{' '}
                {rc.message || ''}
              </li>
            ))}
          </ul>
        )}
        {view.weakSubIndices.length > 0 && (
          <p>
            Weak: {view.weakSubIndices.map(subIndexLabel).join(', ')}
          </p>
        )}
      </section>

      <section className="print-section">
        <h2>Signal quality</h2>
        {data.signal_quality_summary ? (
          <p className="mono muted">
            valid={String(data.signal_quality_summary.valid_fraction ?? '—')} · n=
            {String(data.signal_quality_summary.n_valid_bins ?? '—')} · sar=
            {String(data.signal_quality_summary.sar_fallback_fraction ?? '—')} · mean_q=
            {String(data.signal_quality_summary.mean_bin_quality ?? '—')}
          </p>
        ) : (
          <p className="muted">Not available</p>
        )}
      </section>

      <section className="print-section">
        <h2>Phenology</h2>
        {cycles.length === 0 ? (
          <p className="muted">No cycles</p>
        ) : (
          <ul>
            {cycles.map((c, i) => {
              const ph = c.phenology;
              return (
                <li key={i}>
                  {c.season_label || c.season_type || `Cycle ${i + 1}`}
                  {ph
                    ? `: SOS ${ph.sos ?? '—'} · POS ${ph.pos ?? '—'} · EOS ${ph.eos ?? '—'} · R² ${
                        ph.r2 != null ? formatNumber(ph.r2, 3) : '—'
                      }`
                    : ': phenology n/a'}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="print-section">
        <h2>Performance</h2>
        {seasons.length === 0 ? (
          <p className="muted">No seasonal rows</p>
        ) : (
          <ul>
            {seasons.map((r, i) => {
              const yb = yieldBasisLabel(
                r.yield_detail?.yield_index_basis,
                r.yield_detail?.yield_potential_pct ?? r.yield_potential_pct
              );
              return (
                <li key={i}>
                  {(r.season ?? '').toString()} {r.year ?? ''} · health{' '}
                  {formatNumber(r.health_score)} · {yb.text}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {(data.weather_analysis?.forward_exposure ||
        data.weather_analysis?.backward_resilience) && (
        <section className="print-section">
          <h2>Weather exposure / resilience</h2>
          <p className="muted mono" style={{ whiteSpace: 'pre-wrap', fontSize: '10px' }}>
            {JSON.stringify(
              {
                forward_exposure: data.weather_analysis?.forward_exposure,
                backward_resilience: data.weather_analysis?.backward_resilience,
              },
              null,
              2
            )}
          </p>
        </section>
      )}

      <section className="print-section">
        <h2>Narrative</h2>
        {narrative ? (
          <p style={{ whiteSpace: 'pre-wrap' }}>{narrative}</p>
        ) : (
          <p className="muted">No narrative</p>
        )}
        {data.ai_enrichment?.narrative_source && (
          <p className="muted">Source: {data.ai_enrichment.narrative_source}</p>
        )}
      </section>

      <footer className="print-section muted">
        Generated from assessment job · pipeline {(data.pipeline_stages ?? []).length} stages
        recorded.
      </footer>
    </div>
  );
}
