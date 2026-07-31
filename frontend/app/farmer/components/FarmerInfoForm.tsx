'use client';

import { useState, useEffect } from 'react';
import type { FarmerIdentity } from '../types';
import { LANGUAGE_OPTIONS } from '../types';

interface AgriStackHit {
  farmer_id: string;
  farmer_name: string;
  state?: string | null;
  district?: string | null;
}

interface Props {
  value: FarmerIdentity;
  onChange: (v: FarmerIdentity) => void;
  /** When true, name is not required (e.g. geospatial upload path). */
  optional?: boolean;
}

export function FarmerInfoForm({ value, onChange, optional = false }: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<AgriStackHit[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (query.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(`/api/farm-info?q=${encodeURIComponent(query.trim())}`);
        if (res.ok) {
          const data = await res.json();
          setHits(data.farmers || []);
        }
      } catch {
        setHits([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const set = (patch: Partial<FarmerIdentity>) => onChange({ ...value, ...patch });

  const field =
    'w-full bg-white border border-[#E4DFD4] rounded-lg px-3 py-2.5 text-sm text-stone-800 placeholder-stone-400 focus:outline-none focus:border-emerald-500';

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            Full name{optional ? ' (optional)' : ' *'}
          </label>
          <input
            type="text"
            value={value.farmer_name}
            onChange={(e) => set({ farmer_name: e.target.value })}
            placeholder="e.g. Ramesh Patil"
            className={field}
            required={!optional}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">Phone</label>
          <input
            type="tel"
            value={value.phone}
            onChange={(e) => set({ phone: e.target.value })}
            placeholder="10-digit mobile"
            className={field}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">Language</label>
          <select
            value={value.language}
            onChange={(e) => set({ language: e.target.value })}
            className={field}
          >
            {LANGUAGE_OPTIONS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            AgriStack ID
          </label>
          <input
            type="text"
            value={value.agristack_farmer_id}
            onChange={(e) => set({ agristack_farmer_id: e.target.value })}
            placeholder="Optional ID"
            className={`${field} font-mono`}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-start">
        <div>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search farm_info by name or farmer ID…"
            className={field}
          />
          {searching && <p className="text-[11px] text-stone-500 mt-1">Searching…</p>}
          {hits.length > 0 && (
            <ul className="mt-1 border border-[#E4DFD4] rounded-lg divide-y divide-[#E4DFD4] max-h-36 overflow-y-auto bg-white">
              {hits.map((h) => (
                <li key={h.farmer_id}>
                  <button
                    type="button"
                    onClick={() => {
                      set({
                        agristack_farmer_id: h.farmer_id,
                        farmer_name: value.farmer_name || h.farmer_name,
                      });
                      setQuery(h.farmer_id);
                      setHits([]);
                    }}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-[#F5F2EB] transition-colors"
                  >
                    <span className="font-mono text-emerald-700">{h.farmer_id}</span>
                    <span className="text-stone-500 ml-2">{h.farmer_name}</span>
                    {(h.district || h.state) && (
                      <span className="text-stone-400 text-xs ml-2">
                        {[h.district, h.state].filter(Boolean).join(', ')}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
