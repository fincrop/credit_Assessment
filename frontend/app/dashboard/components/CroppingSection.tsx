import type { AssessmentPayload, CroppingAnalysis } from '../../types/assessment';
import { formatNumber } from '../../lib/format';

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3">
      <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-1.5">{label}</p>
      <p className="font-semibold text-stone-800 text-sm">{value ?? '—'}</p>
    </div>
  );
}

function CropsList({ ca }: { ca: CroppingAnalysis }) {
  const raw = ca.crops_detected;
  if (!raw) return <p className="text-sm text-stone-400">No crop list.</p>;

  if (Array.isArray(raw)) {
    return (
      <div className="flex flex-wrap gap-2">
        {(raw as string[]).map((c, i) => (
          <span key={i} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-700">
            🌾 {c}
          </span>
        ))}
      </div>
    );
  }

  const entries = Object.entries(raw as Record<string, unknown>);
  if (entries.length === 0) return <p className="text-sm text-stone-400">No crops detected.</p>;

  return (
    <div className="overflow-x-auto rounded-lg border border-[#E4DFD4]">
      <table className="w-full text-sm">
        <thead className="bg-[#F5F2EB]">
          <tr>
            {['Crop', 'Detail'].map(h => (
              <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-stone-500 uppercase tracking-wide">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[#E4DFD4]">
          {entries.map(([name, val]) => (
            <tr key={name} className="hover:bg-[#F5F2EB]/60">
              <td className="px-4 py-3 font-semibold text-emerald-700">{name}</td>
              <td className="px-4 py-3 font-mono text-[11px] text-stone-500 break-all">
                {typeof val === 'object' ? JSON.stringify(val) : String(val)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MiniSparkline({ label, values }: { label: string; values: number[] }) {
  const width = 260; const height = 56;
  const min = Math.min(...values);
  const max = Math.max(...values, min + 0.001);
  const points = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * width;
      const y = height - ((v - min) / (max - min)) * height;
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3">
      <p className="text-[10px] font-semibold text-stone-400 uppercase tracking-widest mb-2">{label}</p>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-12" preserveAspectRatio="none">
        <defs>
          <linearGradient id={`grad-${label}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22c55e" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#22c55e" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polyline fill={`url(#grad-${label})`} stroke="#22c55e" strokeWidth="1.5" points={points} />
      </svg>
    </div>
  );
}

export function CroppingSection({ data }: { data: AssessmentPayload }) {
  const ca = data.cropping_analysis;
  const stats = data.continuous_data_stats;
  const series = (ca?.season_results ?? [])
    .flatMap((r) => ((r as Record<string, unknown>).interval_indices as Record<string, unknown>[] | undefined) ?? [])
    .slice(0, 120);

  if (!ca && !stats) {
    return (
      <div className="bg-white rounded-xl border border-[#E4DFD4] p-6">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">Cropping &amp; Satellite Window</h2>
        <p className="text-sm text-stone-400">No cropping analysis in this payload.</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 space-y-5">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">Cropping &amp; Satellite Window</h2>

      {/* Satellite stats */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatCard label="Grid slots" value={stats.grid_slots} />
          <StatCard label="Valid obs." value={stats.valid_observations} />
          <StatCard label="Missing" value={stats.missing_observations} />
          <StatCard label="Interval (d)" value={stats.interval_days} />
          <StatCard label="Span (d)" value={stats.total_days} />
          <StatCard label="Date range" value={
            stats.date_range?.start && stats.date_range?.end
              ? `${stats.date_range.start} → ${stats.date_range.end}`
              : '—'
          } />
        </div>
      )}

      {/* Cropping stats */}
      {ca && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard label="Region" value={ca.region} />
            <StatCard label="Dominant crop" value={ca.dominant_crop} />
            <StatCard label="Intensity" value={formatNumber(ca.cropping_intensity, 2)} />
            <StatCard label="Cultivation signal" value={formatNumber(ca.cultivation_signal, 2)} />
            <StatCard label="Seasons w/ crops" value={ca.seasons_with_crops} />
            <StatCard label="Total seasons" value={ca.total_seasons_analyzed} />
          </div>

          <div>
            <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-3">Crops Detected</p>
            <CropsList ca={ca} />
          </div>

          {series.length > 2 && (
            <div>
              <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-3">Vegetation Index Trends</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {['ndvi', 'evi', 'ndmi'].map((key) => (
                  <MiniSparkline
                    key={key}
                    label={key.toUpperCase()}
                    values={series.map((x) => Number(x[key] ?? 0))}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
