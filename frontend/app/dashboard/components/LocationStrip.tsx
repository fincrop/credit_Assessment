import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber } from '../../lib/format';

export function LocationStrip({ data }: { data: AssessmentPayload }) {
  const loc = data.location;
  const benefits = data.farmer_benefits;

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        {/* Farmer ID */}
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-sm">👤</div>
          <div>
            <p className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider">Farmer</p>
            <p className="text-sm font-semibold text-gray-200 font-mono">{data.farmer_id ?? '—'}</p>
          </div>
        </div>

        <div className="h-8 w-px bg-[#30363d]" />

        {/* Coordinates */}
        {loc?.latitude != null && loc?.longitude != null && (
          <div>
            <p className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider mb-0.5">Location</p>
            <p className="text-xs font-mono text-gray-400">
              {formatNumber(loc.latitude, 4)}°N, {formatNumber(loc.longitude, 4)}°E
            </p>
          </div>
        )}

        {/* Region */}
        {loc?.region && (
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider">Region:</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400">{String(loc.region)}</span>
          </div>
        )}

        {/* Field area */}
        {data.field_area_ha != null && (
          <div>
            <p className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider mb-0.5">Field Area</p>
            <p className="text-sm font-semibold text-gray-200">{formatNumber(data.field_area_ha, 2)} <span className="text-gray-600 text-xs">ha</span></p>
          </div>
        )}

        {/* Timestamp */}
        {data.assessment_date && (
          <div>
            <p className="text-[10px] text-gray-600 font-semibold uppercase tracking-wider mb-0.5">Assessed</p>
            <p className="text-xs text-gray-500 font-mono">{new Date(data.assessment_date).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</p>
          </div>
        )}
      </div>

      {/* Benefits + crop hints */}
      <div className="mt-3 pt-3 border-t border-[#30363d] flex flex-wrap items-center gap-3">
        {benefits && (
          <>
            <span className={`text-[11px] px-2.5 py-1 rounded-full border ${benefits.pm_kisan_enrolled ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500' : 'bg-gray-700/30 border-gray-700 text-gray-600'}`}>
              PM-KISAN: {benefits.pm_kisan_enrolled ? 'Yes' : 'No'}
            </span>
            <span className={`text-[11px] px-2.5 py-1 rounded-full border ${benefits.has_crop_insurance ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500' : 'bg-gray-700/30 border-gray-700 text-gray-600'}`}>
              Crop Insurance: {benefits.has_crop_insurance ? 'Yes' : 'No'}
            </span>
          </>
        )}
        {data.crop_hint && (
          <span className="text-[11px] px-2.5 py-1 rounded-full border bg-amber-500/10 border-amber-500/20 text-amber-500">
            🌾 Crop hint: {data.crop_hint}
          </span>
        )}
        {data.sowing_date && (
          <span className="text-[11px] text-gray-600">
            Sowing (DB): <span className="font-mono text-gray-500">{data.sowing_date}</span>
          </span>
        )}
        {data.ml_mode && (
          <span className="text-[11px] px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-gray-600 font-mono ml-auto">
            mode: {data.ml_mode}
          </span>
        )}
      </div>
    </div>
  );
}
