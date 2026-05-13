import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber, formatPct } from '../../lib/format';

function KpiCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-3">
      <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1.5">{label}</p>
      <p className="font-semibold text-gray-200">{value ?? '—'}</p>
    </div>
  );
}

export function PerformanceSection({ data }: { data: AssessmentPayload }) {
  const pa = data.performance_analysis;

  if (!pa) {
    return (
      <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Crop Performance</h2>
        <p className="text-sm text-gray-600">No performance_analysis in this payload.</p>
      </div>
    );
  }

  const rows = pa.seasonal_performance ?? [];

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6 space-y-5">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Crop Performance (Per Cycle / Season)</h2>

      {/* KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiCard label="Avg health" value={formatNumber(pa.average_health_score)} />
        <KpiCard label="Avg yield score" value={formatNumber(pa.average_yield_score)} />
        <KpiCard label="Avg performance" value={formatNumber(pa.average_performance_score)} />
        <KpiCard label="Seasons scored" value={pa.n_seasons_analyzed} />
        <KpiCard label="Complete cycles" value={pa.n_complete_cycles} />
        <KpiCard label="Active cycles" value={pa.n_active_cycles} />
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-gray-600">No seasonal performance rows.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[#30363d]">
          <table className="w-full text-sm">
            <thead className="bg-[#21262d]">
              <tr>
                {['Season', 'Year', 'Crop', 'Health', 'Yield', 'Method', 'Anomalies'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#30363d]">
              {rows.map((r, i) => {
                const an = r.anomaly_events ?? [];
                const nHigh = an.filter((e) => e.impact === 'HIGH').length;
                const healthNum = typeof r.health_score === 'number' ? r.health_score : 0;
                const healthColor = healthNum >= 65 ? 'text-emerald-400' : healthNum >= 40 ? 'text-amber-400' : 'text-red-400';
                return (
                  <tr key={i} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-3 font-medium text-gray-300">{(r.season ?? '—').toString().toUpperCase()}</td>
                    <td className="px-4 py-3 text-gray-400 font-mono text-xs">{r.year ?? '—'}</td>
                    <td className="px-4 py-3">
                      {r.crop ? (
                        <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-400">{r.crop}</span>
                      ) : '—'}
                    </td>
                    <td className={`px-4 py-3 font-semibold ${healthColor}`}>{formatNumber(r.health_score)}</td>
                    <td className="px-4 py-3 text-gray-400">{formatPct(r.yield_potential_pct)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="px-1.5 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-[10px] text-gray-500 font-mono">
                          {r.scoring_method ?? '—'}
                        </span>
                        {r.is_active_cycle && (
                          <span className="px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/20 text-[10px] text-blue-400">active</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <span className="text-gray-400">{an.length}</span>
                        {nHigh > 0 && (
                          <span className="px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/20 text-[10px] text-red-400">{nHigh} high</span>
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

      {/* Narratives */}
      {rows.some((r) => (r.performance_narrative ?? '').length > 0) && (
        <details className="group">
          <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 transition-colors select-none list-none flex items-center gap-2">
            <span className="group-open:rotate-90 transition-transform">▶</span> Per-cycle narratives
          </summary>
          <ul className="mt-3 space-y-2 pl-4">
            {rows.map((r, i) =>
              r.performance_narrative ? (
                <li key={i} className="text-xs text-gray-600 leading-relaxed">
                  <strong className="text-gray-400">{r.season} {r.year}:</strong> {r.performance_narrative}
                </li>
              ) : null,
            )}
          </ul>
        </details>
      )}
    </div>
  );
}
