'use client';

import type { AssessmentPayload, CropCycle } from '../../types/assessment';
import { formatNumber } from '../../lib/format';

function PhenologyCard({ cycle, index }: { cycle: CropCycle; index: number }) {
  const ph = cycle.phenology;
  const title =
    cycle.season_label ||
    cycle.season_type ||
    `Cycle ${index + 1}`;

  if (!ph) {
    return (
      <div className="bg-paper rounded-lg border border-dashed border-rule p-4">
        <p className="text-sm font-medium text-stone-700 mb-1">{title}</p>
        <p className="text-xs text-stone-500">
          Phenology not available for this cycle (older payload or fit skipped).
        </p>
        {(cycle.sowing_date || cycle.harvest_date) && (
          <p className="text-[11px] text-stone-500 mt-2 font-mono">
            {cycle.sowing_date ?? '?'} → {cycle.harvest_date ?? '?'}
            {cycle.duration_days != null ? ` (${cycle.duration_days}d)` : ''}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="bg-paper rounded-lg border border-rule p-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <p className="text-sm font-medium text-stone-800">{title}</p>
        <span
          className={`text-[10px] px-2 py-0.5 rounded-full border ${
            ph.fit_ok
              ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
              : 'bg-amber-50 border-amber-200 text-amber-900'
          }`}
        >
          {ph.fit_ok ? 'fit ok' : ph.reason || 'fit incomplete'}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-stone-700 mb-2">
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">SOS</p>
          <p className="font-mono">{ph.sos ?? '—'}</p>
        </div>
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">POS</p>
          <p className="font-mono">{ph.pos ?? '—'}</p>
        </div>
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">EOS</p>
          <p className="font-mono">{ph.eos ?? '—'}</p>
        </div>
      </div>
      {ph.r2 != null && (
        <p className="text-[11px] text-stone-500">
          R² <span className="font-mono text-stone-700">{formatNumber(ph.r2, 3)}</span>
        </p>
      )}
    </div>
  );
}

export function CropCyclesSection({ data }: { data: AssessmentPayload }) {
  const cc = data.crop_cycles;

  if (!cc) {
    return (
      <div className="bg-white rounded-xl border border-rule p-6">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">
          Detected Crop Cycles
        </h2>
        <p className="text-sm text-ink-muted">No crop_cycles block in payload.</p>
      </div>
    );
  }

  const um = cc.utilization_metrics ?? {};
  const cycles = cc.cycles ?? [];

  const luiNum = um.land_utilization_index;
  const luiDisplay =
    luiNum != null
      ? luiNum <= 1
        ? `${formatNumber(luiNum * 100)}%`
        : formatNumber(luiNum)
      : '—';

  return (
    <div className="bg-white rounded-xl border border-rule p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
          Land Use &amp; Crop Cycles
        </h2>
        <div className="flex items-center gap-2">
          <span
            className={`text-xs px-2.5 py-1 rounded-full border ${
              cc.detected
                ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                : 'bg-stone-100 border-stone-200 text-stone-500'
            }`}
          >
            {cc.detected ? 'Cycles detected' : 'No cycles'}
          </span>
          <span className="text-xs px-2 py-0.5 rounded bg-paper border border-rule text-stone-500 font-mono">
            {cc.cycles_count ?? cycles.length} cycles
          </span>
          {cc.method && (
            <span className="text-xs px-2 py-0.5 rounded bg-paper border border-rule text-stone-500 font-mono">
              {cc.method}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-paper rounded-lg border border-rule p-4">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1">
            Land Utilization
          </p>
          <p className="text-2xl font-bold text-emerald-700">{luiDisplay}</p>
        </div>
        <div className="bg-paper rounded-lg border border-rule p-4">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1">
            Crops / Year
          </p>
          <p className="text-2xl font-bold text-amber-700">{formatNumber(um.crops_per_year)}</p>
        </div>
        <div className="bg-paper rounded-lg border border-rule p-4">
          <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-widest mb-1">
            Pattern
          </p>
          <p className="text-lg font-semibold text-stone-800">{um.cropping_pattern ?? '—'}</p>
        </div>
      </div>

      {cycles.length === 0 ? (
        <p className="text-sm text-stone-500">No cycle rows to show phenology for.</p>
      ) : (
        <div className="space-y-3">
          <p className="text-[11px] font-semibold text-ink-muted uppercase tracking-wider">
            Phenology
          </p>
          <div className="grid sm:grid-cols-2 gap-3">
            {cycles.map((c, i) => (
              <PhenologyCard key={i} cycle={c} index={i} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
