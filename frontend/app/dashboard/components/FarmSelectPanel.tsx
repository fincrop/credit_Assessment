'use client';

import { PlotBoundaryMap } from './PlotBoundaryMap';

export type SelectableFarm = Record<string, unknown> & {
  plot_key?: string;
  farm_id?: string;
  farm_name?: string;
  area_ha?: number | null;
  geometry?: { type?: string; coordinates?: unknown } | null;
  centroid?: { lat: number; lng: number } | null;
  included_in_assessment?: boolean;
  primary_crop?: string | null;
};

export function FarmSelectPanel({
  farms,
  selectedKeys,
  onToggle,
  onSelectAll,
  onClearAll,
  onAssessSelected,
  onAssessAll,
  onBack,
  busy,
}: {
  farms: SelectableFarm[];
  selectedKeys: Set<string>;
  onToggle: (key: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onAssessSelected: () => void;
  onAssessAll: () => void;
  onBack: () => void;
  busy?: boolean;
}) {
  const keyOf = (f: SelectableFarm, i: number) =>
    String(f.plot_key || f.farm_id || `plot_${i}`);

  const selectedFarm =
    farms.find((f, i) => selectedKeys.has(keyOf(f, i))) || farms[0] || null;

  return (
    <div className="space-y-5 animate-slide-in">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-xl font-bold text-stone-900">Select farms to assess</h2>
          <p className="text-sm text-stone-500 mt-1">
            {selectedKeys.size} of {farms.length} plot(s) selected. Unselected plots are
            stored but skipped by the pipeline.
          </p>
        </div>
        <button
          type="button"
          onClick={onBack}
          disabled={busy}
          className="text-sm text-stone-600 hover:text-stone-900 px-3 py-1.5 rounded-lg border border-rule bg-white"
        >
          ← Change farmer
        </button>
      </div>

      <div className="grid lg:grid-cols-[1fr_1.1fr] gap-5 items-start">
        <div className="bg-white border border-rule rounded-xl p-4 shadow-sm space-y-3">
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={onSelectAll}
              disabled={busy}
              className="text-xs font-medium px-3 py-1.5 rounded-md bg-paper border border-rule text-stone-700 hover:bg-emerald-50"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={onClearAll}
              disabled={busy}
              className="text-xs font-medium px-3 py-1.5 rounded-md bg-paper border border-rule text-stone-700 hover:bg-red-50"
            >
              Clear
            </button>
          </div>
          <ul className="divide-y divide-rule max-h-[420px] overflow-y-auto">
            {farms.map((f, i) => {
              const key = keyOf(f, i);
              const checked = selectedKeys.has(key);
              const area =
                typeof f.area_ha === 'number' && Number.isFinite(f.area_ha)
                  ? `${f.area_ha.toFixed(2)} ha`
                  : '—';
              return (
                <li key={key}>
                  <label className="flex items-start gap-3 py-3 cursor-pointer hover:bg-paper/50 px-1 rounded-md">
                    <input
                      type="checkbox"
                      className="mt-1 accent-emerald-600"
                      checked={checked}
                      disabled={busy}
                      onChange={() => onToggle(key)}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-stone-800">
                        {String(f.farm_name || f.farm_id || key)}
                      </span>
                      <span className="block text-xs font-mono text-stone-500 mt-0.5">
                        {key} · {area}
                        {f.primary_crop ? ` · ${String(f.primary_crop)}` : ''}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="bg-white border border-rule rounded-xl p-3 shadow-sm min-h-[320px]">
          <p className="text-xs font-medium text-stone-500 mb-2 px-1">
            Preview:{' '}
            {selectedFarm
              ? String(selectedFarm.farm_name || selectedFarm.farm_id || 'plot')
              : 'No plot'}
          </p>
          <PlotBoundaryMap
            geometry={
              (selectedFarm?.geometry as {
                type?: string;
                coordinates?: unknown;
              } | null) || null
            }
            centroid={
              (selectedFarm?.centroid as { lat: number; lng: number } | null) || null
            }
            label={
              selectedFarm
                ? String(selectedFarm.farm_name || selectedFarm.farm_id || '')
                : undefined
            }
            minHeight={360}
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy || selectedKeys.size === 0}
          onClick={onAssessSelected}
          className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-200 disabled:text-ink-muted text-white font-bold py-3 px-5 rounded-lg transition-colors"
        >
          {busy ? 'Starting…' : `Assess selected (${selectedKeys.size})`}
        </button>
        <button
          type="button"
          disabled={busy || farms.length === 0}
          onClick={onAssessAll}
          className="bg-white hover:bg-paper border border-rule text-stone-800 font-semibold py-3 px-5 rounded-lg transition-colors disabled:opacity-50"
        >
          Assess all farms
        </button>
      </div>
    </div>
  );
}
