'use client';

import type { ClassifiedFieldProps, FieldStatus } from '../types';
import { FIELD_STATUS_LABELS, classColor } from '../types';

const STATUS_STYLES: Record<FieldStatus, string> = {
  confirmed: 'border-emerald-300 bg-emerald-50 text-emerald-800',
  provisional: 'border-amber-300 bg-amber-50 text-amber-800',
  intercrop: 'border-sky-300 bg-sky-50 text-sky-800',
  abstained: 'border-stone-300 bg-stone-100 text-stone-700',
  not_requested: 'border-stone-300 bg-stone-100 text-stone-700',
  no_cycle: 'border-stone-300 bg-stone-100 text-stone-700',
  no_data: 'border-stone-300 bg-stone-100 text-stone-600',
};

function pct(v: unknown): string | null {
  return typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(0)}%` : null;
}

function text(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null;
}

/**
 * One classified field: the label the map shows, and the model's own answer
 * behind it. When the printed class is Others, the model's own crop is the
 * name in brackets — it is not relabelled as the crop the user asked for.
 */
export function FieldDetail({
  field,
  onClose,
}: {
  field: Record<string, unknown>;
  onClose: () => void;
}) {
  const p = field as ClassifiedFieldProps;
  const crop = String(p.crop ?? 'Unclassified');
  const status = (p.status && p.status in FIELD_STATUS_LABELS ? p.status : null) as FieldStatus | null;
  const top1 = text(p.model_top_crop);
  const top2 = text(p.top2_crop);
  const p1 = pct(p.p_top1);
  const p2 = pct(p.p_top2);
  const margin = typeof p.margin === 'number' && Number.isFinite(p.margin) ? p.margin.toFixed(2) : null;
  const reason = text(p.abstain_reason);
  const note = text(p.note);
  const cycle = [text(p.cycle_sowing), text(p.cycle_peak), text(p.cycle_harvest)];
  const possible = text(p.possible_crop);
  const evidence =
    typeof p.n_obs_optical === 'number' || typeof p.n_obs_radar === 'number'
      ? [
          typeof p.n_obs_optical === 'number' ? `${p.n_obs_optical} optical looks` : null,
          typeof p.n_obs_radar === 'number' ? `${p.n_obs_radar} radar` : null,
          typeof p.frac_imputed === 'number' && p.frac_imputed > 0
            ? `${Math.round(p.frac_imputed * 100)}% gap-filled from radar`
            : null,
        ]
          .filter(Boolean)
          .join(' · ')
      : null;

  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-bold text-stone-900 truncate inline-flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
              style={{ background: classColor(crop) }}
              aria-hidden
            />
            {crop}
            {top1 && top1 !== crop ? ` (${top1})` : ''}
            {status && (
              <span
                className={`ml-1 text-[10px] font-semibold rounded-full border px-1.5 py-px ${STATUS_STYLES[status]}`}
              >
                {FIELD_STATUS_LABELS[status]}
              </span>
            )}
          </p>
          <p className="text-[11px] text-stone-600 mt-0.5">
            Field {String(p.field_id ?? '—')} ·{' '}
            {typeof p.area_ha === 'number' ? `${p.area_ha.toFixed(2)} ha` : '—'} ·{' '}
            {pct(p.confidence) ? `${pct(p.confidence)} model probability` : '—'}
          </p>
        </div>
        <button
          onClick={onClose}
          className="text-stone-400 hover:text-stone-700 text-sm"
          aria-label="Close field details"
        >
          ×
        </button>
      </div>

      {possible && (
        <p className="mt-2 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          Possible {possible}: the model leans {possible} but not confidently enough to print it.
        </p>
      )}
      {evidence && <p className="mt-1 text-[10px] text-stone-500">Evidence: {evidence}</p>}

      {(top1 || top2 || margin) && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[11px] mt-2">
          {top1 && (
            <>
              <dt className="text-stone-500">Model&rsquo;s crop</dt>
              <dd className="text-stone-800 font-medium">
                {top1}
                {p1 ? ` · ${p1}` : ''}
              </dd>
            </>
          )}
          {top2 && (
            <>
              <dt className="text-stone-500">Runner-up</dt>
              <dd className="text-stone-800">
                {top2}
                {p2 ? ` · ${p2}` : ''}
              </dd>
            </>
          )}
          {margin && (
            <>
              <dt className="text-stone-500">Margin</dt>
              <dd className="text-stone-800 tabular-nums">{margin}</dd>
            </>
          )}
          {(typeof p.n_obs_cycle === 'number' || typeof p.cycle_complete === 'boolean') && (
            <>
              <dt className="text-stone-500">Cycle</dt>
              <dd className="text-stone-800">
                {typeof p.n_obs_cycle === 'number' ? `${p.n_obs_cycle} clear looks` : ''}
                {typeof p.cycle_complete === 'boolean'
                  ? `${typeof p.n_obs_cycle === 'number' ? ' · ' : ''}${p.cycle_complete ? 'complete' : 'incomplete'}`
                  : ''}
              </dd>
            </>
          )}
          {cycle.some(Boolean) && (
            <>
              <dt className="text-stone-500">Sow → peak → harvest</dt>
              <dd className="text-stone-800 font-mono">{cycle.map((d) => d || '—').join(' → ')}</dd>
            </>
          )}
          {p.season_consistent === false && (
            <>
              <dt className="text-stone-500">Season</dt>
              <dd className="text-amber-800">Cycle dates do not fit the crop calendar</dd>
            </>
          )}
          {text(p.ecoregion) && (
            <>
              <dt className="text-stone-500">Ecoregion</dt>
              <dd className="text-stone-800 font-mono truncate">{String(p.ecoregion)}</dd>
            </>
          )}
        </dl>
      )}

      {(status === 'not_requested' || status === 'abstained' || crop === 'Others') && top1 && top1 !== crop && (
        <p className="text-[11px] text-stone-600 mt-1.5 leading-relaxed">
          The model leans {top1}. The map shows Others, so this field is not counted as {top1}.
        </p>
      )}
      {reason && (
        <p className="text-[11px] text-stone-700 mt-1.5 leading-relaxed">
          <span className="font-semibold">Why not named:</span> {reason}
        </p>
      )}
      {note && <p className="text-[11px] text-stone-600 mt-1 leading-relaxed">{note}</p>}
    </div>
  );
}
