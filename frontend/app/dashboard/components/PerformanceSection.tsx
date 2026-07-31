'use client';

import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber } from '../../lib/format';
import { yieldBasisLabel } from '../../lib/formatRisk';

function KpiCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3">
      <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">
        {label}
      </p>
      <p className="font-semibold text-stone-800">{value ?? '—'}</p>
    </div>
  );
}

export function PerformanceSection({ data }: { data: AssessmentPayload }) {
  const pa = data.performance_analysis;

  if (!pa) {
    return (
      <div className="bg-white rounded-xl border border-[#E4DFD4] p-6">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">
          Crop Performance
        </h2>
        <p className="text-sm text-stone-400">No performance_analysis in this payload.</p>
      </div>
    );
  }

  const rows = pa.seasonal_performance ?? [];

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 space-y-5">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
        Crop Performance (Per Cycle / Season)
      </h2>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiCard label="Avg health" value={formatNumber(pa.average_health_score)} />
        <KpiCard label="Avg yield score" value={formatNumber(pa.average_yield_score)} />
        <KpiCard label="Avg performance" value={formatNumber(pa.average_performance_score)} />
        <KpiCard label="Seasons scored" value={pa.n_seasons_analyzed} />
        <KpiCard label="Complete cycles" value={pa.n_complete_cycles} />
        <KpiCard label="Active cycles" value={pa.n_active_cycles} />
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-stone-400">No seasonal performance rows.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[#E4DFD4]">
          <table className="w-full text-sm">
            <thead className="bg-[#F5F2EB]">
              <tr>
                {['Season', 'Year', 'Crop', 'Health', 'Yield', 'Method', 'Anomalies'].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-2.5 text-[11px] font-semibold text-stone-500 uppercase tracking-wide"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#E4DFD4]">
              {rows.map((r, i) => {
                const an = r.anomaly_events ?? [];
                const nHigh = an.filter((e) => e.impact === 'HIGH').length;
                const healthNum = typeof r.health_score === 'number' ? r.health_score : 0;
                const healthColor =
                  healthNum >= 65
                    ? 'text-emerald-700'
                    : healthNum >= 40
                      ? 'text-amber-700'
                      : 'text-red-600';
                const basis = r.yield_detail?.yield_index_basis;
                const pct =
                  r.yield_detail?.yield_potential_pct ?? r.yield_potential_pct;
                const yb = yieldBasisLabel(basis, pct);

                return (
                  <tr key={i} className="hover:bg-[#F5F2EB]/60 transition-colors">
                    <td className="px-4 py-3 font-medium text-stone-700">
                      {(r.season ?? '—').toString().toUpperCase()}
                    </td>
                    <td className="px-4 py-3 text-stone-500 font-mono text-xs">
                      {r.year ?? '—'}
                    </td>
                    <td className="px-4 py-3">
                      {r.crop ? (
                        <span className="px-2 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-xs text-emerald-800">
                          {r.crop}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className={`px-4 py-3 font-semibold ${healthColor}`}>
                      {formatNumber(r.health_score)}
                    </td>
                    <td className="px-4 py-3 text-stone-600 text-xs max-w-[14rem]">
                      <span>{yb.text}</span>
                      {yb.kind === 'peer' && (
                        <span className="ml-1.5 inline-flex px-1.5 py-0.5 rounded bg-sky-50 border border-sky-200 text-[10px] text-sky-800">
                          peer
                        </span>
                      )}
                      {yb.kind === 'self' && basis === 'internal_cvi_auc' && (
                        <span className="ml-1.5 inline-flex px-1.5 py-0.5 rounded bg-stone-100 border border-stone-200 text-[10px] text-stone-600">
                          self-calibrated
                        </span>
                      )}
                      {yb.kind === 'self' &&
                        (basis === 'crop_curve_ndvi' || basis === 'crop_specific') && (
                          <span className="ml-1.5 inline-flex px-1.5 py-0.5 rounded bg-stone-100 border border-stone-200 text-[10px] text-stone-600">
                            crop-curve
                          </span>
                        )}
                      {yb.kind === 'self' &&
                        (basis === 'signal_proxy' ||
                          basis === 'signal_only' ||
                          basis === 'cycle_proxy' ||
                          basis === 'signal_based') && (
                          <span className="ml-1.5 inline-flex px-1.5 py-0.5 rounded bg-stone-100 border border-stone-200 text-[10px] text-stone-600">
                            signal-proxy
                          </span>
                        )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="px-1.5 py-0.5 rounded bg-[#F5F2EB] border border-[#E4DFD4] text-[10px] text-stone-500 font-mono">
                          {r.scoring_method ?? '—'}
                        </span>
                        {r.is_active_cycle && (
                          <span className="px-1.5 py-0.5 rounded bg-sky-50 border border-sky-200 text-[10px] text-sky-800">
                            active
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <span className="text-stone-500">{an.length}</span>
                        {nHigh > 0 && (
                          <span className="px-1.5 py-0.5 rounded bg-red-50 border border-red-200 text-[10px] text-red-700">
                            {nHigh} high
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.some((r) => (r.performance_narrative ?? '').length > 0) && (
        <details className="group">
          <summary className="text-xs text-stone-500 cursor-pointer hover:text-stone-700 transition-colors select-none list-none flex items-center gap-2">
            <span className="group-open:rotate-90 transition-transform">▶</span> Per-cycle
            narratives
          </summary>
          <ul className="mt-3 space-y-2 pl-4">
            {rows.map((r, i) =>
              r.performance_narrative ? (
                <li key={i} className="text-xs text-stone-500 leading-relaxed">
                  <strong className="text-stone-700">
                    {r.season} {r.year}:
                  </strong>{' '}
                  {r.performance_narrative}
                </li>
              ) : null
            )}
          </ul>
        </details>
      )}
    </div>
  );
}
