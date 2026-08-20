'use client';

import { useMemo, useState } from 'react';
import {
  farmerLocationParts,
  groupFarmersByDistrict,
  groupFarmersByState,
  UNKNOWN_DISTRICT_KEY,
  UNKNOWN_LOCATION_KEY,
  type FarmerListItem,
  type LocationBucket,
} from '../../lib/farmerLocation';
import { FarmerDetailPanel, FarmerFarmsTable } from './FarmerFarmsTable';
import { vegetationAt } from '../../lib/vizPalette';

function StatChip({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-lg bg-paper/80 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider font-semibold text-ink-muted">{label}</p>
      <p className="text-xl font-bold text-stone-900 tabular-nums leading-tight mt-0.5">{value}</p>
      {hint && <p className="text-[10px] text-ink-muted mt-0.5 truncate">{hint}</p>}
    </div>
  );
}

function LocationBars({
  buckets,
  selectedKey,
  onSelect,
}: {
  buckets: LocationBucket[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.farmerCount));
  if (!buckets.length) {
    return <p className="text-xs text-ink-muted px-1 py-4">No locations in this view.</p>;
  }

  return (
    <ul className="space-y-1">
      {buckets.map((b) => {
        const active = selectedKey === b.key;
        const t = b.farmerCount / max;
        return (
          <li key={b.key}>
            <button
              type="button"
              onClick={() => onSelect(b.key)}
              aria-pressed={active}
              className={`w-full text-left rounded-lg px-2 py-1.5 transition-colors ${
                active ? 'bg-emerald-50 ring-1 ring-emerald-300' : 'hover:bg-paper/90'
              }`}
            >
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className="text-[13px] font-medium text-stone-800 truncate">{b.key}</span>
                <span className="text-[11px] text-ink-muted shrink-0 tabular-nums">
                  {b.farmerCount} · {b.farmCount} farms
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-rule-soft overflow-hidden">
                <div
                  className="h-full rounded-full transition-[width]"
                  style={{
                    width: `${Math.max(8, t * 100)}%`,
                    background: vegetationAt(0.35 + t * 0.55),
                  }}
                />
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function LocationPortfolio({ farmers }: { farmers: FarmerListItem[] }) {
  const [selectedState, setSelectedState] = useState<string | null>(null);
  const [selectedDistrict, setSelectedDistrict] = useState<string | null>(null);
  const [selectedFarmer, setSelectedFarmer] = useState<FarmerListItem | null>(null);

  const stateBuckets = useMemo(() => groupFarmersByState(farmers), [farmers]);

  const districtBuckets = useMemo(() => {
    if (!selectedState) return [];
    return groupFarmersByDistrict(farmers, selectedState);
  }, [farmers, selectedState]);

  const chartBuckets = selectedState ? districtBuckets : stateBuckets;
  const chartSelectedKey = selectedState ? selectedDistrict : selectedState;

  const filtered = useMemo(() => {
    return farmers.filter((f) => {
      const parts = farmerLocationParts(f);
      const stateKey = parts.state || UNKNOWN_LOCATION_KEY;
      const districtKey = parts.district || UNKNOWN_DISTRICT_KEY;
      if (selectedState && stateKey !== selectedState) return false;
      if (selectedDistrict && districtKey !== selectedDistrict) return false;
      return true;
    });
  }, [farmers, selectedState, selectedDistrict]);

  const stats = useMemo(() => {
    const farmCount = filtered.reduce((n, f) => n + (f.farms?.length ?? 0), 0);
    const assessed = filtered.filter((f) => f.has_assessment).length;
    const area = filtered.reduce(
      (n, f) =>
        n +
        (f.farms || []).reduce(
          (s, p) => s + (typeof p.area_ha === 'number' ? p.area_ha : 0),
          0
        ),
      0
    );
    const villages = new Set(
      filtered.map((f) => farmerLocationParts(f).village).filter((v): v is string => !!v)
    ).size;
    return {
      farmers: filtered.length,
      farmCount,
      assessed,
      waiting: filtered.length - assessed,
      area,
      villages,
    };
  }, [filtered]);

  const farmsTitle = selectedDistrict
    ? selectedDistrict
    : selectedState
      ? selectedState
      : 'All locations';

  const handleChartClick = (key: string) => {
    setSelectedFarmer(null);
    if (!selectedState) {
      setSelectedState(key);
      setSelectedDistrict(null);
      return;
    }
    setSelectedDistrict((prev) => (prev === key ? null : key));
  };

  const backToStates = () => {
    setSelectedState(null);
    setSelectedDistrict(null);
    setSelectedFarmer(null);
  };

  return (
    <div className="grid lg:grid-cols-[minmax(280px,36%)_1fr] gap-5 items-start">
      <div className="rounded-2xl border border-rule bg-white shadow-card overflow-hidden lg:sticky lg:top-4">
        <div className="grid grid-cols-2 gap-2 p-3 border-b border-rule">
          <StatChip label="Farmers" value={stats.farmers} />
          <StatChip
            label="Farms"
            value={stats.farmCount}
            hint={stats.area > 0 ? `${stats.area.toFixed(1)} ha` : undefined}
          />
          <StatChip label="Assessed" value={stats.assessed} hint={`${stats.waiting} waiting`} />
          {!selectedState ? (
            <StatChip label="States" value={stateBuckets.length} />
          ) : selectedDistrict ? (
            <StatChip label="Villages" value={stats.villages} />
          ) : (
            <StatChip label="Districts" value={districtBuckets.length} />
          )}
        </div>

        <div className="p-3">
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wider font-semibold text-ink-muted">
                {selectedState ? 'Districts' : 'States'}
              </p>
              <div className="flex items-center gap-1 text-[13px] font-semibold text-stone-900 mt-0.5 min-w-0">
                {selectedState ? (
                  <>
                    <button
                      type="button"
                      onClick={backToStates}
                      className="text-emerald-700 hover:text-emerald-800 font-semibold shrink-0"
                    >
                      States
                    </button>
                    <span className="text-ink-muted font-normal">/</span>
                    <span className="truncate">{selectedState}</span>
                  </>
                ) : (
                  <span>Click a state</span>
                )}
              </div>
            </div>
            {selectedState && (
              <button
                type="button"
                onClick={backToStates}
                className="text-[11px] font-semibold text-ink-2 border border-rule rounded-md px-2 py-1 hover:bg-paper shrink-0"
              >
                All states
              </button>
            )}
          </div>
          <LocationBars
            buckets={chartBuckets}
            selectedKey={chartSelectedKey}
            onSelect={handleChartClick}
          />
        </div>
      </div>

      <div className="min-w-0 space-y-3">
        <div>
          <h3 className="text-base font-bold text-stone-900">{farmsTitle}</h3>
          <p className="text-[12px] text-stone-500 mt-0.5">
            {filtered.length} farmer{filtered.length === 1 ? '' : 's'}
            {selectedState && !selectedDistrict ? ' in this state — pick a district to narrow' : ''}
            {selectedDistrict ? ' in this district' : ''}
            {!selectedState ? ' · pick a state on the left to filter' : ''}. Details opens stored
            analysis; Assess starts a new run.
          </p>
        </div>

        {filtered.length === 0 ? (
          <div className="rounded-xl border border-dashed border-rule bg-white px-6 py-10 text-center text-sm text-stone-500">
            No farmers in this location.
          </div>
        ) : (
          <FarmerFarmsTable
            farmers={filtered}
            selectedId={selectedFarmer?._id}
            onSelect={setSelectedFarmer}
            compact
          />
        )}

        {selectedFarmer && (
          <FarmerDetailPanel selected={selectedFarmer} sticky={false} />
        )}
      </div>
    </div>
  );
}
