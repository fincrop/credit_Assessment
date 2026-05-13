import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber } from '../../lib/format';

export function CropCyclesSection({ data }: { data: AssessmentPayload }) {
  const cc = data.crop_cycles;

  if (!cc) {
    return (
      <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">Detected Crop Cycles</h2>
        <p className="text-sm text-gray-600">No crop_cycles block in payload.</p>
      </div>
    );
  }

  const um = cc.utilization_metrics ?? {};
  const cycles = cc.cycles ?? [];

  const luiNum = um.land_utilization_index;
  const luiDisplay = luiNum != null
    ? (luiNum <= 1 ? `${formatNumber(luiNum * 100)}%` : formatNumber(luiNum))
    : '—';

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Land Use &amp; Crop Cycles</h2>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2.5 py-1 rounded-full border ${cc.detected ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-gray-700/50 border-gray-600 text-gray-500'}`}>
            {cc.detected ? '✓ Cycles detected' : 'No cycles'}
          </span>
          <span className="text-xs px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-gray-500 font-mono">
            {cc.cycles_count ?? cycles.length} cycles
          </span>
          {cc.method && (
            <span className="text-xs px-2 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-gray-600 font-mono">{cc.method}</span>
          )}
        </div>
      </div>

      {/* Utilization KPIs */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
          <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1">Land Utilization</p>
          <p className="text-2xl font-bold text-emerald-400">{luiDisplay}</p>
        </div>
        <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
          <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1">Crops / Year</p>
          <p className="text-2xl font-bold text-amber-400">{formatNumber(um.crops_per_year)}</p>
        </div>
        <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
          <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1">Pattern</p>
          <p className="text-lg font-semibold text-gray-200">{um.cropping_pattern ?? '—'}</p>
        </div>
      </div>

      {/* Raw cycles */}
      {cycles.length > 0 && (
        <details className="group">
          <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 transition-colors select-none list-none flex items-center gap-2">
            <span className="group-open:rotate-90 transition-transform">▶</span>
            Raw cycle objects ({cycles.length})
          </summary>
          <pre className="mt-3 text-[11px] text-emerald-400/80 bg-[#0d1117] rounded-lg border border-[#30363d] p-4 overflow-x-auto leading-relaxed">
            {JSON.stringify(cycles, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
