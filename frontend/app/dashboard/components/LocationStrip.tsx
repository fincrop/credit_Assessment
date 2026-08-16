'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber } from '../../lib/format';
import { useRiskView } from '../../lib/useRiskView';
import { triStateLabel } from '../../lib/formatRisk';

function benefitChipClass(v: boolean | null): string {
  if (v === true) return 'bg-emerald-50 border-emerald-200 text-emerald-800';
  if (v === false) return 'bg-stone-100 border-stone-200 text-stone-600';
  return 'bg-amber-50 border-amber-200 text-amber-900';
}

export function LocationStrip({ data }: { data: AssessmentPayload }) {
  const loc = data.location;
  const view = useRiskView(data);
  const { pm_kisan, has_crop_insurance } = view.benefits;

  return (
    <div className="bg-white rounded-xl border border-rule p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-emerald-100 border border-emerald-200 flex items-center justify-center text-sm">
            👤
          </div>
          <div>
            <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider">
              Farmer
            </p>
            <p className="text-sm font-semibold text-stone-800 font-mono">
              {data.farmer_id ?? '—'}
            </p>
          </div>
        </div>

        <div className="h-8 w-px bg-rule" />

        {loc?.latitude != null && loc?.longitude != null && (
          <div>
            <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider mb-0.5">
              Location
            </p>
            <p className="text-xs font-mono text-stone-500">
              {formatNumber(loc.latitude, 4)}°N, {formatNumber(loc.longitude, 4)}°E
            </p>
          </div>
        )}

        {loc?.region && (
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider">
              Region:
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-sky-50 border border-sky-200 text-sky-800">
              {String(loc.region)}
            </span>
          </div>
        )}

        {data.field_area_ha != null && (
          <div>
            <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider mb-0.5">
              Field Area
            </p>
            <p className="text-sm font-semibold text-stone-800">
              {formatNumber(
                view.totalScoredAreaHa ?? data.field_area_ha,
                2
              )}{' '}
              <span className="text-ink-muted text-xs">ha</span>
            </p>
          </div>
        )}

        {view.nPlotsTotal != null && (
          <div>
            <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider mb-0.5">
              Plots
            </p>
            <p className="text-sm font-semibold text-stone-800">
              {view.nPlotsScored ?? 0}/{view.nPlotsTotal} scored
              {view.nPlotsFailed ? (
                <span className="text-amber-700 text-xs font-normal">
                  {' '}
                  · {view.nPlotsFailed} failed
                </span>
              ) : null}
            </p>
          </div>
        )}

        {data.assessment_date && (
          <div>
            <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider mb-0.5">
              Assessed
            </p>
            <p className="text-xs text-stone-500 font-mono">
              {new Date(data.assessment_date).toLocaleString('en-IN', {
                dateStyle: 'medium',
                timeStyle: 'short',
              })}
            </p>
          </div>
        )}
      </div>

      <div className="mt-3 pt-3 border-t border-rule flex flex-wrap items-center gap-3">
        <span
          className={`text-[11px] px-2.5 py-1 rounded-full border ${benefitChipClass(pm_kisan)}`}
        >
          PM-KISAN: {triStateLabel(pm_kisan)}
        </span>
        <span
          className={`text-[11px] px-2.5 py-1 rounded-full border ${benefitChipClass(has_crop_insurance)}`}
        >
          Crop Insurance: {triStateLabel(has_crop_insurance)}
        </span>
        {data.crop_hint && (
          <span className="text-[11px] px-2.5 py-1 rounded-full border bg-amber-50 border-amber-200 text-amber-800">
            Crop hint: {data.crop_hint}
          </span>
        )}
        {data.sowing_date && (
          <span className="text-[11px] text-ink-muted">
            Sowing (DB):{' '}
            <span className="font-mono text-stone-500">{data.sowing_date}</span>
          </span>
        )}
        {data.ml_mode && (
          <span className="text-[11px] px-2 py-0.5 rounded bg-paper border border-rule text-stone-500 font-mono ml-auto">
            mode: {data.ml_mode}
          </span>
        )}
      </div>
    </div>
  );
}
