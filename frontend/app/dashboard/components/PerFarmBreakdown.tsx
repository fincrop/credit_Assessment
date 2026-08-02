'use client';

import { useState } from 'react';
import type { FarmAssessment } from '../../types/assessment';
import { formatScoreOne, formatScoreWhole } from '../../lib/formatRisk';
import { riskBgClass } from '../../lib/format';

function tenureBadge(f: FarmAssessment): { label: string; className: string } {
  if (!f.included) {
    return { label: 'Skipped', className: 'bg-stone-100 text-stone-500 border-stone-200' };
  }
  if (f.is_ror_owner === false) {
    return { label: 'Leased', className: 'bg-amber-50 text-amber-800 border-amber-200' };
  }
  if (f.tenure_factor < 0.99 && f.tenure_factor > 0) {
    return { label: 'Joint', className: 'bg-sky-50 text-sky-800 border-sky-200' };
  }
  return { label: 'Owned', className: 'bg-emerald-50 text-emerald-800 border-emerald-200' };
}

export function PerFarmBreakdown({
  farms,
  onSelectFarm,
  selectedFarmId,
}: {
  farms: FarmAssessment[];
  onSelectFarm?: (farmId: string | null) => void;
  selectedFarmId?: string | null;
}) {
  const [openId, setOpenId] = useState<string | null>(null);

  if (!farms?.length) return null;

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-5">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-1">
        Per-farm breakdown
      </h2>
      <p className="text-xs text-stone-500 mb-4">
        Each plot scored individually; farmer index aggregates tenure-weighted sub-indices.
      </p>
      <ul className="divide-y divide-[#E4DFD4] border border-[#E4DFD4] rounded-lg overflow-hidden">
        {farms.map((f, i) => {
          const id = f.farm_id || `plot_${i}`;
          const open = openId === id;
          const selected = selectedFarmId === id;
          const badge = tenureBadge(f);
          const topReason = f.reason_codes?.[0]?.message;
          const dimmed = !f.included;

          return (
            <li
              key={id}
              className={`${dimmed ? 'bg-stone-50/80' : 'bg-white'} ${
                selected ? 'ring-1 ring-inset ring-emerald-300' : ''
              }`}
            >
              <button
                type="button"
                className="w-full text-left px-4 py-3 flex flex-wrap items-center gap-3 hover:bg-[#F5F2EB]/60"
                onClick={() => {
                  setOpenId(open ? null : id);
                  onSelectFarm?.(selected ? null : id);
                }}
              >
                <span className="font-mono text-xs text-stone-600 min-w-[7rem]">{id}</span>
                <span
                  className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold ${badge.className}`}
                >
                  {badge.label}
                </span>
                {f.crop && (
                  <span className="text-xs text-stone-500">{f.crop}</span>
                )}
                <span className="text-xs text-stone-400">
                  {formatScoreOne(f.area_ha)} ha · w={formatScoreOne(f.tenure_factor)}
                </span>
                <span className="ml-auto flex items-center gap-2">
                  {f.included && f.risk_category && (
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full border font-bold ${riskBgClass(
                        f.risk_category
                      )}`}
                    >
                      {f.risk_category}
                    </span>
                  )}
                  <span
                    className={`text-sm font-semibold tabular-nums ${
                      dimmed ? 'text-stone-400' : 'text-stone-800'
                    }`}
                  >
                    {f.included ? formatScoreWhole(f.index_score ?? null) : '—'}
                  </span>
                </span>
              </button>
              {(open || f.skipped_reason) && (
                <div className="px-4 pb-3 text-xs text-stone-500 space-y-1">
                  {f.skipped_reason && (
                    <p className="text-amber-800">Skipped: {f.skipped_reason}</p>
                  )}
                  {topReason && <p>{topReason}</p>}
                  {f.included && f.sub_indices && Object.keys(f.sub_indices).length > 0 && (
                    <p className="font-mono text-[11px] text-stone-400">
                      {Object.entries(f.sub_indices)
                        .map(([k, v]) => `${k}:${formatScoreOne(v)}`)
                        .join(' · ')}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
