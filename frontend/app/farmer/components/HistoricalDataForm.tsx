'use client';

import { TriStateSelect } from '../../components/TriStateSelect';
import type { FarmerExtras, FarmPolygon, FarmerIdentity, FarmerLocation } from '../types';
import { CROP_OPTIONS, IRRIGATION_OPTIONS, SEASON_OPTIONS } from '../types';

interface Props {
  value: FarmerExtras;
  onChange: (v: FarmerExtras) => void;
  identity: FarmerIdentity;
  location: FarmerLocation;
  farms: FarmPolygon[];
  /** When true, omit the duplicate page heading + review summary (single-page layout). */
  hideReview?: boolean;
}

export function HistoricalDataForm({
  value,
  onChange,
  identity,
  location,
  farms,
  hideReview = false,
}: Props) {
  const set = (patch: Partial<FarmerExtras>) => onChange({ ...value, ...patch });

  const addRow = () => {
    set({
      historical_data: [
        ...value.historical_data,
        { year: '2024-2025', season: 'Kharif', crop: 'Rice', yield_estimate_kg_ha: '' },
      ],
    });
  };

  const updateRow = (idx: number, patch: Partial<(typeof value.historical_data)[0]>) => {
    const rows = value.historical_data.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    set({ historical_data: rows });
  };

  const removeRow = (idx: number) => {
    set({ historical_data: value.historical_data.filter((_, i) => i !== idx) });
  };

  const inputCls =
    'w-full bg-white border border-rule rounded-lg px-3 py-2 text-sm text-stone-800 focus:outline-none focus:border-emerald-500';

  return (
    <div className="space-y-6">
      {!hideReview && (
        <div>
          <h2 className="text-lg font-bold text-stone-900 mb-1">Historical Data &amp; Review</h2>
          <p className="text-sm text-stone-500">Past seasons, irrigation, and benefits — then submit.</p>
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-stone-700">Past seasons</h3>
          <button
            type="button"
            onClick={addRow}
            className="text-xs font-medium text-emerald-700 hover:text-emerald-600"
          >
            + Add row
          </button>
        </div>
        {value.historical_data.length === 0 && (
          <p className="text-xs text-stone-500">No historical rows yet (optional).</p>
        )}
        {value.historical_data.map((row, idx) => (
          <div key={idx} className="grid sm:grid-cols-5 gap-2 items-end">
            <input
              className={inputCls}
              placeholder="Year"
              value={row.year}
              onChange={(e) => updateRow(idx, { year: e.target.value })}
            />
            <select
              className={inputCls}
              value={row.season}
              onChange={(e) => updateRow(idx, { season: e.target.value })}
            >
              {SEASON_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select
              className={inputCls}
              value={row.crop}
              onChange={(e) => updateRow(idx, { crop: e.target.value })}
            >
              {CROP_OPTIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <input
              type="number"
              className={inputCls}
              placeholder="Yield kg/ha"
              value={row.yield_estimate_kg_ha}
              onChange={(e) =>
                updateRow(idx, {
                  yield_estimate_kg_ha: e.target.value === '' ? '' : Number(e.target.value),
                })
              }
            />
            <button
              type="button"
              onClick={() => removeRow(idx)}
              className="text-xs text-red-600 hover:text-red-500 py-2"
            >
              Remove
            </button>
          </div>
        ))}
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-stone-700 mb-1.5">Irrigation type</label>
          <select
            className={inputCls}
            value={value.irrigation_type}
            onChange={(e) => set({ irrigation_type: e.target.value })}
          >
            <option value="">Select…</option>
            {IRRIGATION_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-stone-700 mb-1.5">
            Soil type (optional)
          </label>
          <input
            className={inputCls}
            value={value.soil_type}
            onChange={(e) => set({ soil_type: e.target.value })}
            placeholder="e.g. Black cotton, Alluvial"
          />
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <TriStateSelect
          compact
          label="PM-KISAN enrolled"
          value={value.pm_kisan_enrolled}
          onChange={(pm_kisan_enrolled) => set({ pm_kisan_enrolled })}
        />
        <TriStateSelect
          compact
          label="Crop insurance (PMFBY)"
          value={value.has_crop_insurance}
          onChange={(has_crop_insurance) => set({ has_crop_insurance })}
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-stone-700 mb-1.5">Notes</label>
        <textarea
          className={`${inputCls} min-h-[80px]`}
          value={value.notes}
          onChange={(e) => set({ notes: e.target.value })}
          placeholder="Optional comments…"
        />
      </div>

      {!hideReview && (
        <div className="bg-paper border border-rule rounded-xl p-4 space-y-2 text-sm">
          <h3 className="font-semibold text-stone-800 mb-2">Review summary</h3>
          <p>
            <span className="text-stone-500">Name:</span> {identity.farmer_name || '—'}
          </p>
          {identity.agristack_farmer_id && (
            <p>
              <span className="text-stone-500">AgriStack ID:</span>{' '}
              <span className="font-mono text-emerald-700">{identity.agristack_farmer_id}</span>
            </p>
          )}
          <p>
            <span className="text-stone-500">Location:</span>{' '}
            {[
              location.village?.name,
              location.taluka?.name,
              location.district?.name,
              location.state?.name,
            ]
              .filter(Boolean)
              .join(', ') || '—'}
          </p>
          <p>
            <span className="text-stone-500">Farms:</span> {farms.length} (
            {farms.reduce((s, f) => s + f.area_ha, 0).toFixed(3)} ha)
          </p>
          <ul className="text-xs text-stone-500 pl-4 list-disc">
            {farms.map((f) => (
              <li key={f.farm_id}>
                {f.farm_name} — {f.primary_crop} — {f.area_ha.toFixed(3)} ha
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
