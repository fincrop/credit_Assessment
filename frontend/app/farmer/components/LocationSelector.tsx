'use client';

import { useState, useEffect, useCallback } from 'react';
import type { FarmerLocation, LgdSelection } from '../types';
import { SearchableSelect } from './SearchableSelect';

interface Props {
  value: FarmerLocation;
  onChange: (v: FarmerLocation) => void;
  /** Soften required markers when farms/upload already provide location. */
  optional?: boolean;
}

interface LocItem {
  lgd_code: string;
  name: string;
}

export function LocationSelector({ value, onChange, optional = false }: Props) {
  const [states, setStates] = useState<LocItem[]>([]);
  const [districts, setDistricts] = useState<LocItem[]>([]);
  const [talukas, setTalukas] = useState<LocItem[]>([]);
  const [villages, setVillages] = useState<LocItem[]>([]);
  const [villageQ, setVillageQ] = useState('');
  const [loading, setLoading] = useState<string | null>(null);
  const [geoMsg, setGeoMsg] = useState<string | null>(null);
  const [talukaSource, setTalukaSource] = useState<string>('');

  useEffect(() => {
    fetch('/api/location/states')
      .then((r) => r.json())
      .then((d) => setStates(d.states || []))
      .catch(() => setStates([]));
  }, []);

  useEffect(() => {
    if (!value.state?.lgd_code) {
      setDistricts([]);
      return;
    }
    setLoading('districts');
    fetch(`/api/location/districts?state_code=${encodeURIComponent(value.state.lgd_code)}`)
      .then((r) => r.json())
      .then((d) => setDistricts(d.districts || []))
      .catch(() => setDistricts([]))
      .finally(() => setLoading(null));
  }, [value.state?.lgd_code]);

  useEffect(() => {
    if (!value.district?.lgd_code) {
      setTalukas([]);
      return;
    }
    setLoading('talukas');
    fetch(`/api/location/talukas?district_code=${encodeURIComponent(value.district.lgd_code)}`)
      .then((r) => r.json())
      .then((d) => {
        setTalukas(d.talukas || []);
        setTalukaSource(d.source || '');
      })
      .catch(() => setTalukas([]))
      .finally(() => setLoading(null));
  }, [value.district?.lgd_code]);

  useEffect(() => {
    if (!value.taluka?.lgd_code && !value.district?.lgd_code) {
      setVillages([]);
      return;
    }
    const t = setTimeout(() => {
      setLoading('villages');
      const params = new URLSearchParams();
      if (value.taluka?.lgd_code) params.set('taluka_code', value.taluka.lgd_code);
      else if (value.district?.lgd_code) params.set('district_code', value.district.lgd_code);
      if (villageQ) params.set('q', villageQ);
      fetch(`/api/location/villages?${params}`)
        .then((r) => r.json())
        .then((d) => setVillages(d.villages || []))
        .catch(() => setVillages([]))
        .finally(() => setLoading(null));
    }, 250);
    return () => clearTimeout(t);
  }, [value.taluka?.lgd_code, value.district?.lgd_code, villageQ]);

  const pick = useCallback(
    (level: keyof FarmerLocation, item: LgdSelection | null) => {
      const next: FarmerLocation = { ...value, [level]: item };
      if (level === 'state') {
        next.district = null;
        next.taluka = null;
        next.village = null;
      } else if (level === 'district') {
        next.taluka = null;
        next.village = null;
      } else if (level === 'taluka') {
        next.village = null;
      }
      onChange(next);
    },
    [value, onChange]
  );

  const geocodeAndCenter = async (parts: string[], base: FarmerLocation) => {
    const q = parts.filter(Boolean).join(', ') + ', India';
    try {
      const res = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`,
        { headers: { Accept: 'application/json' } }
      );
      const data = await res.json();
      if (data?.[0]) {
        onChange({
          ...base,
          mapCenter: { lat: parseFloat(data[0].lat), lng: parseFloat(data[0].lon) },
        });
      }
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    const parts = [
      value.village?.name,
      value.taluka?.name,
      value.district?.name,
      value.state?.name,
    ].filter(Boolean) as string[];
    if (parts.length >= 2) {
      const t = setTimeout(() => geocodeAndCenter(parts, value), 400);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.state?.name, value.district?.name, value.taluka?.name, value.village?.name]);

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setGeoMsg('Geolocation is not supported in this browser.');
      return;
    }
    setGeoMsg('Detecting location…');
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        const { fillLocationFromCoords } = await import('../lib/fillLocationFromCoords');
        const filled = await fillLocationFromCoords(latitude, longitude, value);
        onChange(filled);
        setGeoMsg(
          filled.state || filled.district
            ? `Filled from GPS: ${[filled.village?.name, filled.taluka?.name, filled.district?.name, filled.state?.name].filter(Boolean).join(', ')}`
            : `Map centered at ${latitude.toFixed(4)}, ${longitude.toFixed(4)}.`
        );
      },
      () => setGeoMsg('Could not get your location. Check browser permissions.'),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const inputCls =
    'w-full bg-white border border-[#E4DFD4] rounded-lg px-3 py-2.5 text-sm text-stone-800 focus:outline-none focus:border-emerald-500 disabled:opacity-40';

  const req = optional ? '' : ' *';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-stone-500">
          {optional
            ? 'Optional when boundaries are drawn or uploaded — auto-filled from map coordinates when possible.'
            : 'Select administrative location, or use GPS / draw-upload to auto-fill.'}
        </p>
        <button
          type="button"
          onClick={useMyLocation}
          className="text-xs font-medium px-3 py-2 rounded-lg border border-[#E4DFD4] text-sky-700 hover:bg-sky-50 transition-colors"
        >
          Use my location
        </button>
      </div>
      {geoMsg && <p className="text-xs text-stone-500">{geoMsg}</p>}

      <div className="grid grid-cols-1 gap-3">
        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            State{req}
          </label>
          <SearchableSelect
            options={states.map((s) => ({
              value: s.lgd_code,
              label: s.name,
              meta: `LGD ${s.lgd_code}`,
            }))}
            value={value.state?.lgd_code || ''}
            onChange={(code) => {
              const s = states.find((x) => x.lgd_code === code);
              pick('state', s ? { lgd_code: s.lgd_code, name: s.name } : null);
            }}
            placeholder="Search state…"
          />
          {value.state && !value.state.lgd_code && (
            <p className="text-[10px] text-stone-500 mt-1">{value.state.name}</p>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            District{req}
          </label>
          <SearchableSelect
            options={districts.map((d) => ({
              value: d.lgd_code,
              label: d.name,
              meta: `LGD ${d.lgd_code}`,
            }))}
            value={value.district?.lgd_code || ''}
            onChange={(code) => {
              const d = districts.find((x) => x.lgd_code === code);
              pick('district', d ? { lgd_code: d.lgd_code, name: d.name } : null);
            }}
            placeholder="Search district…"
            disabled={!value.state}
            loading={loading === 'districts'}
          />
          {value.district && !value.district.lgd_code && (
            <p className="text-[10px] text-stone-500 mt-1">{value.district.name}</p>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            Taluka / Sub-district
          </label>
          <SearchableSelect
            options={talukas.map((t) => ({
              value: t.lgd_code,
              label: t.name,
              meta: `LGD ${t.lgd_code}`,
            }))}
            value={value.taluka?.lgd_code || ''}
            onChange={(code) => {
              const t = talukas.find((x) => x.lgd_code === code);
              pick('taluka', t ? { lgd_code: t.lgd_code, name: t.name } : null);
            }}
            placeholder={talukas.length === 0 ? 'Type taluka name…' : 'Search taluka…'}
            disabled={!value.district}
            loading={loading === 'talukas'}
            allowFreeText={talukas.length === 0}
            freeTextValue={!value.taluka?.lgd_code ? value.taluka?.name : undefined}
            onFreeText={(text) => {
              if (talukas.length === 0) {
                pick('taluka', text ? { lgd_code: '', name: text } : null);
              }
            }}
          />
          {talukaSource && (
            <p className="text-[10px] text-stone-400 mt-1">Source: {talukaSource}</p>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">Village</label>
          <input
            className={`${inputCls} mb-2`}
            placeholder="Search or type village…"
            value={villageQ || (!value.village?.lgd_code ? value.village?.name || '' : '')}
            onChange={(e) => {
              setVillageQ(e.target.value);
              if (villages.length === 0) {
                pick('village', e.target.value ? { lgd_code: '', name: e.target.value } : null);
              }
            }}
            disabled={!value.district && !optional}
          />
          {villages.length > 0 ? (
            <SearchableSelect
              options={villages.map((v) => ({
                value: v.lgd_code,
                label: v.name,
                meta: `LGD ${v.lgd_code}`,
              }))}
              value={value.village?.lgd_code || ''}
              onChange={(code) => {
                const v = villages.find((x) => x.lgd_code === code);
                pick('village', v ? { lgd_code: v.lgd_code, name: v.name } : null);
              }}
              placeholder="Select village…"
            />
          ) : (
            <p className="text-[10px] text-stone-400">
              {loading === 'villages' ? 'Searching villages…' : 'Free-text village is saved if DB has no match.'}
            </p>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-600 mb-1">
            Katha / Survey number
          </label>
          <input
            className={inputCls}
            value={value.katha_number}
            onChange={(e) => onChange({ ...value, katha_number: e.target.value })}
            placeholder="Optional"
          />
        </div>
      </div>
    </div>
  );
}
