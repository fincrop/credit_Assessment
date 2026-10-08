'use client';

import { useState, type ReactNode } from 'react';
import {
  RECORD_STATUS_LABELS,
  RECORD_STATUS_STYLES,
  type FieldRecord,
  type MonsoonOnset,
  type PhenologyCandidate,
  type RecordStatus,
} from '../types';
import { triggerDownload } from './DownloadPanel';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function month(date: string | null | undefined): string {
  const m = Number(String(date || '').slice(5, 7));
  return m >= 1 && m <= 12 ? MONTHS[m - 1] : '—';
}

function num(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

function candidateText(c: PhenologyCandidate): string {
  if (typeof c === 'string') return c;
  const metric = typeof c.ratio === 'number' ? c.ratio : typeof c.score === 'number' ? c.score : null;
  return `${c.crop || '?'}${metric !== null ? ` ${metric.toFixed(2)}` : ''}`;
}

const QA_LABELS: Record<string, string> = {
  phenology_disagrees_with_class: 'Phenology disagrees with class',
  few_clear_looks: 'Few clear looks',
  edge_pixels_in_statistics: 'Edge pixels in statistics',
  named_from_reference: 'Named from the cotton or soybean reference curve',
};

const PIXEL_BASIS_LABELS: Record<string, string> = {
  interior: 'interior pixels',
  inner: 'inner pixels (edge ring kept)',
  full: 'all pixels incl. edges',
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-stone-500">{label}</dt>
      <dd className="text-stone-800 min-w-0">{children}</dd>
    </>
  );
}

function Sub({ children }: { children: ReactNode }) {
  return <span className="block text-[10px] text-stone-500 leading-snug">{children}</span>;
}

/**
 * The bank-facing record of one field from the raster engine. A field
 * without enough evidence says so instead of showing a default.
 */
export function FieldRecordCard({
  record,
  jobId,
  onset,
  onClose,
}: {
  record: FieldRecord;
  jobId: string;
  onset?: MonsoonOnset | null;
  onClose?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = (record.status && record.status in RECORD_STATUS_LABELS ? record.status : null) as RecordStatus | null;
  const sowing = record.sowing || {};
  const harvest = record.harvest || {};
  const stress = record.stress || {};
  const yld = record.yield || null;
  const check = record.phenology_check || null;

  const downloadReport = async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(
        `/api/monitoring/download/${jobId}?format=report&field_id=${encodeURIComponent(record.field_id)}`,
        { credentials: 'include' }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Server returned ${res.status}`);
      }
      triggerDownload(await res.blob(), `farm_report_${record.field_id.replace(/[^\w.-]+/g, '_')}.png`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Report download failed.');
    } finally {
      setBusy(false);
    }
  };

  let sowingText: ReactNode;
  if (sowing.status === 'insufficient_evidence') {
    sowingText = `Not estimated — window ${sowing.p10 || '—'}–${sowing.p90 || '—'}`;
  } else if (sowing.status === 'before_window') {
    sowingText = sowing.note || 'Canopy already up at the start of the season window';
  } else if (sowing.status === 'no_cue') {
    sowingText = 'No cue';
  } else if (sowing.date) {
    sowingText = (
      <>
        <span className="font-medium">{sowing.date}</span>
        {sowing.status === 'provided' ? ' · from the farm record' : ''}
        {sowing.p10 || sowing.p90 ? <Sub>P10–P90 {sowing.p10 || '—'} → {sowing.p90 || '—'}</Sub> : null}
        {sowing.sources?.length ? <Sub>Sources: {sowing.sources.join(', ')}</Sub> : null}
      </>
    );
  } else {
    sowingText = 'Not estimated';
  }

  const stageText =
    record.stage === 'disputed' ? (
      <>
        <span className="font-medium text-red-800">disputed</span>
        {record.stage_if_alternative?.stage ? (
          <Sub>
            If {record.stage_if_alternative.crop || 'the alternative crop'}: {record.stage_if_alternative.stage}
          </Sub>
        ) : null}
      </>
    ) : (
      <>
        {record.stage || '—'}
        {typeof record.das === 'number' ? ` · ${record.das} DAS` : ''}
      </>
    );

  const win = harvest.window || null;
  let harvestText: ReactNode = '—';
  if (harvest.observed && harvest.date) {
    harvestText = `Harvested ${harvest.date}`;
  } else if (win?.kind === 'multi_pick' && (win.start || win.end)) {
    harvestText = (
      <>
        Picking {month(win.start)}–{month(win.end)}
        <Sub>{win.start || '—'} → {win.end || '—'}</Sub>
      </>
    );
  } else if (win && (win.start || win.end)) {
    harvestText = `${win.start || '—'} → ${win.end || '—'}`;
  }

  let conditionText: ReactNode;
  if (stress.status === 'condition_unavailable') {
    conditionText = 'Condition unavailable';
  } else if (stress.latest_class) {
    conditionText = (
      <>
        <span className="font-medium capitalize">{stress.latest_class}</span>
        {stress.latest_type ? ` · ${stress.latest_type}` : ''}
        <span className={stress.latest_confirmed ? 'text-emerald-700' : 'text-amber-700'}>
          {stress.latest_confirmed ? ' · confirmed' : ' · unconfirmed'}
        </span>
        <Sub>
          {stress.latest_date || '—'}
          {typeof stress.looks_scored === 'number'
            ? ` · ${stress.looks_stressed ?? 0} of ${stress.looks_scored} looks stressed`
            : ''}
          {typeof stress.confirmed_looks === 'number' ? `, ${stress.confirmed_looks} confirmed` : ''}
        </Sub>
        {stress.reference ? <Sub>Reference: {stress.reference}</Sub> : null}
      </>
    );
  } else {
    conditionText = '—';
  }

  let yieldText: ReactNode = '—';
  if (yld) {
    const main =
      yld.basis === 'withheld' ? (
        <span className="text-stone-600">Withheld</span>
      ) : typeof yld.yield_t_ha === 'number' ? (
        <>
          <span className="font-medium">{num(yld.yield_t_ha)} t/ha</span>
          {` (P10–P90 ${num(yld.yield_p10)}–${num(yld.yield_p90)})`}
          {yld.baseline_source ? <Sub>Baseline: {yld.baseline_source}</Sub> : null}
        </>
      ) : typeof yld.yield_index === 'number' ? (
        <>
          <span className="font-medium">Index {num(yld.yield_index)}</span>
          {` (P10–P90 ${num(yld.index_p10)}–${num(yld.index_p90)})`}
          <Sub>Relative to village median</Sub>
        </>
      ) : (
        <span className="text-stone-600">Not estimated</span>
      );
    yieldText = (
      <>
        {main}
        {yld.label ? <Sub>{yld.label}</Sub> : null}
        {yld.note ? <Sub>{yld.note}</Sub> : null}
      </>
    );
  }

  return (
    <article className="rounded-xl border border-emerald-200 bg-emerald-50/50 px-3 py-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-bold text-stone-900 flex flex-wrap items-center gap-1.5">
            {record.crop}
            {status && (
              <span className={`text-[10px] font-semibold rounded-full border px-1.5 py-px ${RECORD_STATUS_STYLES[status]}`}>
                {RECORD_STATUS_LABELS[status]}
              </span>
            )}
          </p>
          <p className="text-[11px] text-stone-500">
            Field {record.field_id}
            {typeof record.area_ha === 'number' ? ` · ${record.area_ha.toFixed(2)} ha` : ''}
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-stone-400 hover:text-stone-700 text-sm"
            aria-label="Close field record"
          >
            ×
          </button>
        )}
      </div>

      {status === 'phenology_disagrees' && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-2 py-1.5 text-[11px] text-red-800 leading-relaxed">
          Phenology suggests {check?.best_alternative || 'another crop'}
          {check?.candidates?.length ? ` (${check.candidates.map(candidateText).join(', ')})` : ''}
          {check?.reason ? `. ${check.reason}` : ''}
        </p>
      )}

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[11px]">
        <Row label="Confidence">
          {typeof record.confidence === 'number' ? (
            <>
              <span className="font-medium">{Math.round(record.confidence * 100)}%</span>
              {record.confidence_basis === 'reference_curve' ? (
                <Sub>From the reference curve, not the classifier probability</Sub>
              ) : record.confidence_basis === 'model' ? (
                <Sub>From the classifier</Sub>
              ) : null}
              {record.reference_note ? <Sub>{record.reference_note}</Sub> : null}
            </>
          ) : (
            '—'
          )}
        </Row>
        <Row label="Sowing">
          {sowingText}
          {sowing.regime ? <Sub>Regime: {sowing.regime.replace(/_/g, ' ')}</Sub> : null}
          {sowing.note ? <Sub>{sowing.note}</Sub> : null}
        </Row>
        {(sowing.onset || onset?.date) && (
          <Row label="Monsoon onset">
            {sowing.onset || onset?.date}
            {typeof onset?.cumulative_mm === 'number' ? ` · ${Math.round(onset.cumulative_mm)} mm` : ''}
          </Row>
        )}
        <Row label="Stage">{stageText}</Row>
        {record.peak && <Row label="Peak">{record.peak}</Row>}
        <Row label="Harvest">{harvestText}</Row>
        <Row label="Condition">{conditionText}</Row>
        <Row label="Yield">{yieldText}</Row>
        <Row label="Data quality">
          {typeof record.n_clear_looks === 'number' ? `${record.n_clear_looks} clear looks` : '—'}
          {record.last_clear_observation ? ` · last clear ${record.last_clear_observation}` : ''}
          <Sub>
            {PIXEL_BASIS_LABELS[record.pixel_basis || ''] || record.pixel_basis || 'pixel basis unknown'}
            {typeof record.n_interior_pixels === 'number' ? ` · ${record.n_interior_pixels} interior pixels` : ''}
          </Sub>
        </Row>
      </dl>

      {record.low_resolution && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900 leading-relaxed">
          Small field for 10 m pixels: the raster here is village-scale context, not a within-field map.
        </p>
      )}

      {!!record.qa_flags?.length && (
        <div className="flex flex-wrap gap-1">
          {record.qa_flags.map((flag) => (
            <span
              key={flag}
              className="rounded-full border border-stone-300 bg-white px-1.5 py-px text-[10px] text-stone-700"
              title={flag}
            >
              {QA_LABELS[flag] || flag.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={() => void downloadReport()}
        disabled={busy}
        className="w-full rounded-lg border border-emerald-600 bg-white text-emerald-700 text-xs font-semibold py-1.5 hover:bg-emerald-50 disabled:opacity-60"
      >
        {busy ? 'Preparing…' : 'Download farm report (PNG)'}
      </button>
      {error && <p className="text-[11px] text-red-700">{error}</p>}
    </article>
  );
}
