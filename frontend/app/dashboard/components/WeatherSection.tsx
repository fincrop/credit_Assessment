'use client';

import type { AssessmentPayload, WeatherIndicators } from '../../types/assessment';
import { formatNumber } from '../../lib/format';
import { readWeatherIndicator } from '../../lib/formatRisk';

function MiniCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3">
      <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">
        {label}
      </p>
      <p className="font-semibold text-stone-800">{value ?? '—'}</p>
    </div>
  );
}

function IndicatorsBlock({
  title,
  ind,
}: {
  title: string;
  ind: WeatherIndicators | undefined;
}) {
  if (!ind || ind.available === false) {
    return (
      <div className="bg-[#F5F2EB] rounded-lg border border-dashed border-[#E4DFD4] p-3">
        <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-1">
          {title}
        </p>
        <p className="text-xs text-stone-500">Weather indicators not available for this cycle.</p>
      </div>
    );
  }

  const dry = readWeatherIndicator(ind, 'max_dry_spell_days', 'dry_spell_max_days');
  const wet = readWeatherIndicator(ind, 'max_wet_spell_days', 'wet_spell_max_days');
  const gdd = readWeatherIndicator(ind, 'gdd_total', 'gdd');
  const onset = readWeatherIndicator(
    ind,
    'monsoon_onset_offset_days',
    'monsoon_onset_anomaly_days'
  );
  const heat = readWeatherIndicator(ind, 'heat_stress_days');
  const cold = readWeatherIndicator(ind, 'cold_stress_days');
  const spi = readWeatherIndicator(ind, 'spi_like');

  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3">
      <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
        {title}
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs text-stone-700">
        <span>
          Dry spell: <strong className="font-mono">{formatNumber(dry)}</strong> d
        </span>
        <span>
          Heat stress: <strong className="font-mono">{formatNumber(heat)}</strong> d
        </span>
        <span>
          SPI-like: <strong className="font-mono">{formatNumber(spi, 2)}</strong>
        </span>
        {wet != null && (
          <span>
            Wet spell: <strong className="font-mono">{formatNumber(wet)}</strong> d
          </span>
        )}
        {cold != null && (
          <span>
            Cold stress: <strong className="font-mono">{formatNumber(cold)}</strong> d
          </span>
        )}
        {gdd != null && (
          <span>
            GDD: <strong className="font-mono">{formatNumber(gdd)}</strong>
          </span>
        )}
        {onset != null && (
          <span>
            Monsoon onset offset:{' '}
            <strong className="font-mono">{formatNumber(onset)}</strong> d
          </span>
        )}
      </div>
    </div>
  );
}

function RecordPanel({
  title,
  obj,
}: {
  title: string;
  obj: Record<string, unknown> | undefined;
}) {
  if (!obj || Object.keys(obj).length === 0) {
    return (
      <div className="bg-[#F5F2EB] rounded-lg border border-dashed border-[#E4DFD4] p-4">
        <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-1">
          {title}
        </p>
        <p className="text-xs text-stone-500">Not present in this payload.</p>
      </div>
    );
  }

  const entries = Object.entries(obj).filter(
    ([, v]) => v != null && typeof v !== 'object'
  );

  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-4">
      <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
        {title}
      </p>
      {entries.length === 0 ? (
        <pre className="text-[11px] text-stone-600 overflow-x-auto">
          {JSON.stringify(obj, null, 2)}
        </pre>
      ) : (
        <div className="grid grid-cols-2 gap-2 text-xs text-stone-700">
          {entries.slice(0, 12).map(([k, v]) => (
            <span key={k}>
              {k.replace(/_/g, ' ')}:{' '}
              <strong className="font-mono">
                {typeof v === 'number' ? formatNumber(v, 2) : String(v)}
              </strong>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function WeatherSection({ data }: { data: AssessmentPayload }) {
  const wa = data.weather_analysis;
  const intervalBlocks = data.weather_intervals ?? [];

  if (!wa) {
    return (
      <div className="bg-white rounded-xl border border-[#E4DFD4] p-6">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">
          Weather &amp; Climate Stress
        </h2>
        <p className="text-sm text-stone-400">No weather_analysis in this payload.</p>
      </div>
    );
  }

  const cycles = wa.cycle_risk_scores ?? [];
  const events = wa.extreme_events ?? [];
  const seasonal = wa.seasonal_weather ?? [];

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 space-y-5">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
        Weather &amp; Climate Stress
      </h2>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <MiniCard label="Weather risk score" value={formatNumber(wa.weather_risk_score)} />
        <MiniCard label="Extreme events" value={wa.total_extreme_events ?? '—'} />
        <MiniCard label="Critical-stage events" value={wa.critical_stage_events ?? '—'} />
        <MiniCard label="Kharif rain (mm)" value={formatNumber(wa.kharif_avg_rainfall_mm)} />
        <MiniCard label="Rabi rain (mm)" value={formatNumber(wa.rabi_avg_rainfall_mm)} />
      </div>

      {(wa.weather_sources_used?.length ?? 0) > 0 && (
        <p className="text-xs text-stone-500">
          Sources:{' '}
          <span className="font-mono text-stone-700">
            {wa.weather_sources_used!.join(', ')}
          </span>
        </p>
      )}

      <div className="grid sm:grid-cols-2 gap-3">
        <RecordPanel title="Forward exposure" obj={wa.forward_exposure} />
        <RecordPanel title="Backward resilience" obj={wa.backward_resilience} />
      </div>

      {seasonal.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider">
            Per-season weather indicators
          </p>
          {seasonal.map((s, i) => (
            <IndicatorsBlock
              key={i}
              title={`${(s.season ?? 'season').toString()} ${s.year ?? ''}`.trim()}
              ind={s.weather_indicators}
            />
          ))}
        </div>
      )}

      {cycles.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Per-Cycle Weather Risk
          </p>
          <div className="overflow-x-auto rounded-lg border border-[#E4DFD4]">
            <table className="w-full text-sm">
              <thead className="bg-[#F5F2EB]">
                <tr>
                  {['Cycle', 'Risk Score', 'Events'].map((h) => (
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
                {cycles.map((c, i) => (
                  <tr key={i} className="hover:bg-[#F5F2EB]/60">
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-500">
                      {c.cycle_id ?? `cycle_${i}`}
                    </td>
                    <td className="px-4 py-3 text-stone-700">{formatNumber(c.risk_score)}</td>
                    <td className="px-4 py-3 text-stone-500">{c.n_events ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {events.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Extreme Events (sample)
          </p>
          <div className="overflow-x-auto rounded-lg border border-[#E4DFD4]">
            <table className="w-full text-sm">
              <thead className="bg-[#F5F2EB]">
                <tr>
                  {['Type', 'Severity', 'Date'].map((h) => (
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
                {events.slice(0, 25).map((e, i) => {
                  const ev = e as Record<string, unknown>;
                  return (
                    <tr key={i} className="hover:bg-[#F5F2EB]/60">
                      <td className="px-4 py-3 text-stone-700">{String(ev.type ?? '—')}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`text-xs px-2 py-0.5 rounded-full border ${
                            String(ev.severity ?? '').toLowerCase() === 'high'
                              ? 'bg-red-50 text-red-700 border-red-200'
                              : 'bg-amber-50 text-amber-800 border-amber-200'
                          }`}
                        >
                          {String(ev.severity ?? '—')}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-[11px] text-stone-500">
                        {String(ev.date ?? ev.date_or_start ?? '—')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {events.length > 25 && (
              <p className="px-4 py-2.5 text-[11px] text-stone-400 border-t border-[#E4DFD4]">
                Showing 25 of {events.length} events.
              </p>
            )}
          </div>
        </div>
      )}

      {intervalBlocks.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Weather Events by Crop Interval
          </p>
          <div className="overflow-x-auto rounded-lg border border-[#E4DFD4]">
            <table className="w-full text-sm">
              <thead className="bg-[#F5F2EB]">
                <tr>
                  {['Cycle', 'Window', 'Risk', 'Events'].map((h) => (
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
                {intervalBlocks.map((w, i) => (
                  <tr key={i} className="hover:bg-[#F5F2EB]/60">
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-500">
                      {w.cycle_id ?? '-'}
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-500">
                      {w.start_date ?? '?'} → {w.end_date ?? '?'}
                    </td>
                    <td className="px-4 py-3 text-stone-700">
                      {formatNumber(w.weather_risk)}
                    </td>
                    <td className="px-4 py-3 text-stone-500">
                      {w.event_count ?? w.events?.length ?? 0}
                    </td>
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
