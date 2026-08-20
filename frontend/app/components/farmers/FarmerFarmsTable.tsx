'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  cropList,
  farmerLocationParts,
  formatShortDate,
  pipelineIdOf,
  sourceLabel,
  totalAreaHa,
  type FarmerListItem,
} from '../../lib/farmerLocation';
import { farmerAssessHref, farmerResultsHref } from '../../lib/farmerRoutes';
import { bandChipStyle, bandForIndex, bandForRiskCategory, toKbsScore } from '../../lib/kbsScore';

export function FarmerFarmsTable({
  farmers,
  selectedId,
  onSelect,
  onDelete,
  showManageActions = false,
  compact = false,
}: {
  farmers: FarmerListItem[];
  selectedId?: string | null;
  onSelect?: (farmer: FarmerListItem) => void;
  onDelete?: (id: string) => void;
  showManageActions?: boolean;
  /** Hide state/district columns when the parent already filters by location. */
  compact?: boolean;
}) {
  const cell = compact ? 'px-3 py-2.5' : 'px-4 py-3';
  return (
    <div className="overflow-x-auto border border-rule rounded-xl bg-white">
      <table className="w-full text-sm text-left">
        <thead className="bg-paper-raised text-stone-500 text-xs uppercase tracking-wider">
          <tr>
            <th className={`${cell} font-semibold`}>Name</th>
            {!compact && <th className={`${cell} font-semibold`}>State</th>}
            {!compact && <th className={`${cell} font-semibold`}>District</th>}
            <th className={`${cell} font-semibold`}>Village</th>
            <th className={`${cell} font-semibold`}>Farms</th>
            <th className={`${cell} font-semibold`}>Area</th>
            {!compact && <th className={`${cell} font-semibold`}>Crops</th>}
            <th className={`${cell} font-semibold`}>Assessment</th>
            {!compact && <th className={`${cell} font-semibold`}>Date</th>}
            <th className={`${cell} font-semibold`}>Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {farmers.map((f) => {
            const loc = farmerLocationParts(f);
            const crops = cropList(f);
            const area = totalAreaHa(f);
            const assessed = !!f.has_assessment;
            const pid = pipelineIdOf(f);
            const band =
              bandForIndex(f.index_score) || bandForRiskCategory(f.risk_category);
            const kbs = toKbsScore(f.index_score);
            return (
              <tr
                key={f._id}
                className={`hover:bg-paper/70 ${onSelect ? 'cursor-pointer' : ''} ${
                  selectedId === f._id ? 'bg-emerald-50/70' : ''
                }`}
                onClick={() => onSelect?.(f)}
              >
                <td className={cell}>
                  <p className="font-medium text-stone-900">{f.farmer_name || 'Unnamed farmer'}</p>
                  <p className="text-[11px] text-ink-muted mt-0.5">
                    {sourceLabel(f)}
                    {compact && crops.length ? ` · ${crops.join(', ')}` : ''}
                    {!compact && f.agristack_farmer_id ? ` · ${f.agristack_farmer_id}` : ''}
                  </p>
                </td>
                {!compact && <td className={`${cell} text-stone-600`}>{loc.state || '—'}</td>}
                {!compact && <td className={`${cell} text-stone-600`}>{loc.district || '—'}</td>}
                <td className={`${cell} text-stone-600`}>{loc.village || '—'}</td>
                <td className={`${cell} font-mono text-stone-700`}>{f.farms?.length ?? 0}</td>
                <td className={`${cell} font-mono text-stone-700`}>
                  {area != null ? `${area.toFixed(2)} ha` : '—'}
                </td>
                {!compact && (
                  <td className={`${cell} text-stone-600 max-w-[160px] truncate`}>
                    {crops.join(', ') || '—'}
                  </td>
                )}
                <td className={cell}>
                  {assessed ? (
                    <div className="flex flex-col gap-1">
                      <span className="text-[11px] font-semibold text-emerald-800 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded w-fit">
                        Assessed
                      </span>
                      {(kbs != null || f.risk_category) && (
                        <span
                          className="text-[11px] font-semibold px-2 py-0.5 rounded border w-fit"
                          style={bandChipStyle(band)}
                        >
                          {kbs != null ? `KBS ${kbs}` : f.risk_category}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-[11px] text-ink-muted">Not assessed</span>
                  )}
                </td>
                {!compact && (
                  <td className={`${cell} text-stone-500 text-xs whitespace-nowrap`}>
                    {formatShortDate(f.latest_assessment_date || f.updated_at || f.created_at)}
                  </td>
                )}
                <td className={cell} onClick={(e) => e.stopPropagation()}>
                  <div className={`flex ${compact ? 'flex-row flex-wrap gap-2' : 'flex-col gap-2'} items-start`}>
                    {assessed && (
                      <Link
                        href={farmerResultsHref(pid)}
                        className="text-[11px] font-semibold text-emerald-800 bg-emerald-50 border border-emerald-200 px-2.5 py-1 rounded-md hover:bg-emerald-100"
                      >
                        Details
                      </Link>
                    )}
                    <Link
                      href={farmerAssessHref(pid)}
                      className="text-[11px] font-semibold text-sky-800 bg-sky-50 border border-sky-200 px-2.5 py-1 rounded-md hover:bg-sky-100"
                    >
                      {assessed ? 'Assess again' : 'Assess'}
                    </Link>
                    {showManageActions && (
                      <>
                        <Link
                          href={`/farmer?edit=${encodeURIComponent(f._id)}`}
                          className="text-xs font-semibold text-stone-500 hover:text-stone-800"
                        >
                          Edit
                        </Link>
                        {onDelete && (
                          <button
                            type="button"
                            onClick={() => onDelete(f._id)}
                            className="text-xs text-red-500 hover:text-red-600"
                          >
                            Delete
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function FarmerDetailPanel({
  selected,
  onDelete,
  showManageActions = false,
  sticky = true,
}: {
  selected: FarmerListItem | null;
  onDelete?: (id: string) => void;
  showManageActions?: boolean;
  sticky?: boolean;
}) {
  const [openPlots, setOpenPlots] = useState(true);
  if (!selected) {
    return (
      <aside className={`bg-white border border-rule rounded-xl p-4 h-fit ${sticky ? 'sticky top-20' : ''}`}>
        <h2 className="text-sm font-semibold text-stone-700 mb-3">Farm details</h2>
        <p className="text-xs text-ink-muted">Select a farmer to see plot-level information.</p>
      </aside>
    );
  }

  const loc = farmerLocationParts(selected);
  const pid = pipelineIdOf(selected);
  const area = totalAreaHa(selected);
  const crops = cropList(selected);
  const assessed = !!selected.has_assessment;
  const kbs = toKbsScore(selected.index_score);
  const band = bandForIndex(selected.index_score) || bandForRiskCategory(selected.risk_category);

  return (
    <aside className={`bg-white border border-rule rounded-xl p-4 h-fit ${sticky ? 'sticky top-20' : ''}`}>
      <h2 className="text-sm font-semibold text-stone-700 mb-3">Farm details</h2>
      <div className="space-y-3 text-sm">
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Name</p>
          <p className="text-stone-900 font-medium">{selected.farmer_name || 'Unnamed farmer'}</p>
        </div>
        {selected.agristack_farmer_id && (
          <div>
            <p className="text-[10px] text-ink-muted uppercase tracking-wider">AgriStack ID</p>
            <p className="font-mono text-emerald-700 text-xs">{selected.agristack_farmer_id}</p>
          </div>
        )}
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Location</p>
          <p className="text-stone-600 text-xs leading-relaxed">
            {[loc.village, loc.district, loc.state].filter(Boolean).join(', ') || '—'}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <p className="text-[10px] text-ink-muted uppercase tracking-wider">Farms</p>
            <p className="font-mono text-stone-800">{selected.farms?.length ?? 0}</p>
          </div>
          <div>
            <p className="text-[10px] text-ink-muted uppercase tracking-wider">Area</p>
            <p className="font-mono text-stone-800">{area != null ? `${area.toFixed(2)} ha` : '—'}</p>
          </div>
        </div>
        <div>
          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Crops</p>
          <p className="text-stone-600 text-xs">{crops.join(', ') || '—'}</p>
        </div>
        {assessed && (
          <div>
            <p className="text-[10px] text-ink-muted uppercase tracking-wider">Latest score</p>
            {kbs != null || selected.risk_category ? (
              <p className="text-xs font-semibold mt-0.5" style={{ color: band?.ink }}>
                {kbs != null ? `KBS ${kbs}` : ''}
                {selected.risk_category ? ` · ${selected.risk_category}` : ''}
              </p>
            ) : (
              <p className="text-xs text-emerald-700 mt-0.5">Assessed</p>
            )}
            {selected.latest_assessment_date && (
              <p className="text-[11px] text-ink-muted mt-0.5">
                {new Date(selected.latest_assessment_date).toLocaleString('en-IN', {
                  dateStyle: 'medium',
                  timeStyle: 'short',
                })}
              </p>
            )}
          </div>
        )}

        <div>
          <button
            type="button"
            onClick={() => setOpenPlots((v) => !v)}
            className="text-[10px] text-ink-muted uppercase tracking-wider font-semibold mb-1"
          >
            Plots {openPlots ? '▾' : '▸'}
          </button>
          {openPlots && (
            <ul className="space-y-1.5">
              {(selected.farms || []).map((farm, i) => (
                <li key={farm.farm_id || i} className="text-xs text-stone-600 leading-snug">
                  <span className="font-mono text-stone-800">
                    {farm.farm_name || farm.farm_id || `Farm ${i + 1}`}
                  </span>
                  <span className="text-ink-muted">
                    {' '}
                    · {farm.primary_crop || 'crop —'} ·{' '}
                    {farm.area_ha != null ? `${Number(farm.area_ha).toFixed(2)} ha` : 'area —'}
                  </span>
                </li>
              ))}
              {(selected.farms || []).length === 0 && (
                <li className="text-xs text-ink-muted">No plots stored.</li>
              )}
            </ul>
          )}
        </div>

        <div className="flex flex-col gap-2 pt-2">
          {assessed && (
            <Link
              href={farmerResultsHref(pid)}
              className="text-center text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white py-2 rounded-lg"
            >
              View analysis
            </Link>
          )}
          <Link
            href={farmerAssessHref(pid)}
            className="text-center text-xs font-semibold border border-rule bg-white hover:bg-paper text-stone-700 py-2 rounded-lg"
          >
            {assessed ? 'Assess again' : 'Run assessment'}
          </Link>
          {showManageActions && (
            <>
              <Link
                href={`/farmer?edit=${encodeURIComponent(selected._id)}`}
                className="text-center text-xs font-semibold bg-paper hover:bg-stone-100 text-stone-600 py-2 rounded-lg"
              >
                Edit in wizard
              </Link>
              {onDelete && (
                <button
                  type="button"
                  onClick={() => onDelete(selected._id)}
                  className="text-center text-xs font-semibold text-red-500 hover:text-red-600 py-2"
                >
                  Delete
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </aside>
  );
}
