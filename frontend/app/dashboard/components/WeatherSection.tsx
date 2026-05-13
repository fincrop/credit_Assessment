import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber } from '../../lib/format';

function MiniCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-3">
      <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1.5">{label}</p>
      <p className="font-semibold text-gray-200">{value ?? '—'}</p>
    </div>
  );
}

export function WeatherSection({ data }: { data: AssessmentPayload }) {
  const wa = data.weather_analysis;
  const intervalBlocks = data.weather_intervals ?? [];

  if (!wa) {
    return (
      <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Weather &amp; Climate Stress</h2>
        <p className="text-sm text-gray-600">No weather_analysis in this payload.</p>
      </div>
    );
  }

  const cycles = wa.cycle_risk_scores ?? [];
  const events = wa.extreme_events ?? [];

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6 space-y-5">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Weather &amp; Climate Stress</h2>

      {/* KPIs */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <MiniCard label="Weather risk score" value={formatNumber(wa.weather_risk_score)} />
        <MiniCard label="Extreme events" value={wa.total_extreme_events ?? '—'} />
        <MiniCard label="Critical-stage events" value={wa.critical_stage_events ?? '—'} />
        <MiniCard label="Kharif rain (mm)" value={formatNumber(wa.kharif_avg_rainfall_mm)} />
        <MiniCard label="Rabi rain (mm)" value={formatNumber(wa.rabi_avg_rainfall_mm)} />
      </div>

      {/* Per-cycle risk */}
      {cycles.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Per-Cycle Weather Risk</p>
          <div className="overflow-x-auto rounded-lg border border-[#30363d]">
            <table className="w-full text-sm">
              <thead className="bg-[#21262d]">
                <tr>
                  {['Cycle', 'Risk Score', 'Events'].map(h => (
                    <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#30363d]">
                {cycles.map((c, i) => (
                  <tr key={i} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-3 font-mono text-[11px] text-gray-400">{c.cycle_id ?? `cycle_${i}`}</td>
                    <td className="px-4 py-3 text-gray-300">{formatNumber(c.risk_score)}</td>
                    <td className="px-4 py-3 text-gray-400">{c.n_events ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Extreme events */}
      {events.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Extreme Events (sample)</p>
          <div className="overflow-x-auto rounded-lg border border-[#30363d]">
            <table className="w-full text-sm">
              <thead className="bg-[#21262d]">
                <tr>
                  {['Type', 'Severity', 'Date'].map(h => (
                    <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#30363d]">
                {events.slice(0, 25).map((e, i) => {
                  const ev = e as Record<string, unknown>;
                  return (
                    <tr key={i} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 py-3 text-gray-300">{String(ev.type ?? '—')}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${
                          String(ev.severity ?? '').toLowerCase() === 'high'
                            ? 'bg-red-500/10 text-red-400 border-red-500/20'
                            : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                        }`}>
                          {String(ev.severity ?? '—')}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-[11px] text-gray-500">
                        {String(ev.date ?? ev.date_or_start ?? '—')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {events.length > 25 && (
              <p className="px-4 py-2.5 text-[11px] text-gray-600 border-t border-[#30363d]">
                Showing 25 of {events.length} events — see Raw JSON tab for full list.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Interval blocks */}
      {intervalBlocks.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Weather Events by Crop Interval</p>
          <div className="overflow-x-auto rounded-lg border border-[#30363d]">
            <table className="w-full text-sm">
              <thead className="bg-[#21262d]">
                <tr>
                  {['Cycle', 'Window', 'Risk', 'Events'].map(h => (
                    <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[#30363d]">
                {intervalBlocks.map((w, i) => (
                  <tr key={i} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-3 font-mono text-[11px] text-gray-400">{w.cycle_id ?? '-'}</td>
                    <td className="px-4 py-3 font-mono text-[11px] text-gray-500">{w.start_date ?? '?'} → {w.end_date ?? '?'}</td>
                    <td className="px-4 py-3 text-gray-300">{formatNumber(w.weather_risk)}</td>
                    <td className="px-4 py-3 text-gray-400">{w.event_count ?? w.events?.length ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
