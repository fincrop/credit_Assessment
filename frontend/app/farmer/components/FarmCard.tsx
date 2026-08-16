'use client';

import { useState } from 'react';
import type { FarmPolygon } from '../types';
import { CROP_OPTIONS } from '../types';

import { MAP_COLORS } from '../../lib/mapStyle';
interface Props {
  farm: FarmPolygon;
  onDelete: () => void;
  onEdit: (updated: FarmPolygon) => void;
}

export function FarmCard({ farm, onDelete, onEdit }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({
    farm_name: farm.farm_name,
    farm_number: farm.farm_number || '',
    primary_crop: farm.primary_crop,
    sowing_date: farm.sowing_date || '',
  });

  const ring = farm.boundary?.coordinates?.[0] || [];

  const saveEdit = () => {
    onEdit({
      ...farm,
      farm_name: draft.farm_name.trim() || farm.farm_name,
      farm_number: draft.farm_number.trim(),
      primary_crop: draft.primary_crop,
      sowing_date: draft.sowing_date,
    });
    setEditing(false);
  };

  return (
    <div className="bg-white border border-rule rounded-xl p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="w-3 h-3 rounded-sm flex-shrink-0"
            style={{ background: farm.color || MAP_COLORS.boundary }}
          />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-stone-900 truncate">{farm.farm_name}</h3>
            {farm.farm_number && (
              <p className="text-[10px] text-stone-500 font-mono">{farm.farm_number}</p>
            )}
          </div>
        </div>
        <div className="flex gap-1 flex-shrink-0">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-xs text-stone-500 hover:text-stone-700 px-1.5 py-0.5"
          >
            {expanded ? 'Hide' : 'Expand'}
          </button>
          <button
            type="button"
            onClick={() => {
              setDraft({
                farm_name: farm.farm_name,
                farm_number: farm.farm_number || '',
                primary_crop: farm.primary_crop,
                sowing_date: farm.sowing_date || '',
              });
              setEditing((e) => !e);
            }}
            className="text-xs text-stone-500 hover:text-sky-400 px-1.5 py-0.5"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="text-xs text-stone-500 hover:text-red-400 px-1.5 py-0.5"
          >
            Delete
          </button>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-ink-muted">Crop</dt>
          <dd className="text-stone-700">{farm.primary_crop || '—'}</dd>
        </div>
        <div>
          <dt className="text-ink-muted">Area</dt>
          <dd className="text-stone-700 font-mono">{farm.area_ha?.toFixed(3) ?? '—'} ha</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-ink-muted">Sowing</dt>
          <dd className="text-stone-700">{farm.sowing_date || '—'}</dd>
        </div>
      </dl>

      {editing && (
        <div className="mt-3 space-y-2 border-t border-rule pt-3">
          <input
            className="w-full bg-white border border-rule rounded px-2 py-1.5 text-xs text-stone-800"
            value={draft.farm_name}
            onChange={(e) => setDraft({ ...draft, farm_name: e.target.value })}
            placeholder="Farm name"
          />
          <input
            className="w-full bg-white border border-rule rounded px-2 py-1.5 text-xs text-stone-800"
            value={draft.farm_number}
            onChange={(e) => setDraft({ ...draft, farm_number: e.target.value })}
            placeholder="Farm / Katha number"
          />
          <select
            className="w-full bg-white border border-rule rounded px-2 py-1.5 text-xs text-stone-800"
            value={draft.primary_crop}
            onChange={(e) => setDraft({ ...draft, primary_crop: e.target.value })}
          >
            {CROP_OPTIONS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input
            type="date"
            className="w-full bg-white border border-rule rounded px-2 py-1.5 text-xs text-stone-800"
            value={draft.sowing_date}
            onChange={(e) => setDraft({ ...draft, sowing_date: e.target.value })}
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={saveEdit}
              className="text-xs bg-emerald-600 text-white px-3 py-1 rounded"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-xs text-stone-500"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {expanded && (
        <div className="mt-3 border-t border-rule pt-3 space-y-2">
          <p className="text-[10px] text-stone-500">
            Centroid: {farm.centroid.lat.toFixed(5)}, {farm.centroid.lng.toFixed(5)}
          </p>
          <p className="text-[10px] text-ink-muted font-semibold uppercase tracking-wider">
            Vertices ({Math.max(0, ring.length - 1)})
          </p>
          <pre className="text-[10px] font-mono text-stone-500 max-h-28 overflow-y-auto bg-paper rounded p-2 border border-rule">
            {ring.slice(0, -1).map((c, i) => `${i + 1}. ${c[1]?.toFixed(6)}, ${c[0]?.toFixed(6)}`).join('\n') || '—'}
          </pre>
        </div>
      )}
    </div>
  );
}
