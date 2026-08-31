'use client';

import Link from 'next/link';
import type { FarmAssessment } from '../../types/assessment';
import { plotKeyOf } from '../../lib/plotKey';
import { formatScoreWhole } from '../../lib/formatRisk';
import { bandForRiskCategory, bandChipStyle } from '../../lib/kbsScore';
import { terminalStateOfFarm, landCoverLabel } from '../../lib/terminalState';
import {
  areaMismatchOf,
  areaMismatchHeadline,
  areaMismatchSkipReason,
  formatHa,
  geometryAreaHa,
} from '../../lib/areaMismatch';
import {
  isAreaMismatchSkip,
  isMonitoringAreaTooSmall,
  monitoringAreaTooSmallMessages,
} from '../../lib/plotSkipMessage';

export type StreamRowStatus = 'pending' | 'analyzing' | 'scored' | 'skipped' | 'failed';

export type StreamFarmRow = {
  plot_key: string;
  farm_id?: string;
  area_ha?: number;
  measured_area_ha?: number | null;
  geometry?: unknown;
  crop?: string | null;
  is_ror_owner?: boolean | null;
  tenure_factor?: number;
  status: StreamRowStatus;
  assessment?: FarmAssessment | null;
};

function tenureLabel(row: StreamFarmRow): string {
  if (row.is_ror_owner === true) return 'Owned';
  if (row.is_ror_owner === false) return 'Leased / joint';
  if ((row.tenure_factor ?? 1) < 1) return 'Partial tenure';
  return 'Tenure —';
}

/**
 * Why a plot was left out, in words a loan officer can act on.
 *
 * The raw `skipped_reason` is a machine token (`not_agricultural:WATER`), and
 * showing it raw makes a legitimate exclusion look like a crash. The three
 * kinds have genuinely different consequences — one is a finding about the
 * land, one is a finding about our view of it, one is our bug — so they get
 * different words and different tones.
 */
function ExclusionNote({
  farm,
  registeredHa,
  measuredHa,
  geometry,
}: {
  farm: FarmAssessment;
  registeredHa?: number;
  measuredHa?: number | null;
  geometry?: unknown;
}) {
  const v = terminalStateOfFarm(farm);
  const measured =
    measuredHa ?? farm.measured_area_ha ?? geometryAreaHa(geometry);
  const mismatch = areaMismatchOf({
    registeredHa: registeredHa ?? farm.area_ha,
    measuredHa: measured,
    viability: farm.parcel_viability,
  });

  if (isMonitoringAreaTooSmall(farm, measured)) {
    const { detail, skip } = monitoringAreaTooSmallMessages(farm, measured);
    return (
      <div className="mt-1 space-y-0.5">
        <p className="text-[11px] leading-snug" style={{ color: '#7A5405' }}>
          {detail}
        </p>
        <p className="text-[11px] leading-snug font-medium" style={{ color: '#7A5405' }}>
          {skip}
        </p>
      </div>
    );
  }

  if (mismatch && isAreaMismatchSkip(farm)) {
    return (
      <div className="mt-1 space-y-0.5">
        <p className="text-[11px] leading-snug" style={{ color: '#7A5405' }}>
          {areaMismatchHeadline(mismatch)}
        </p>
        <p className="text-[11px] leading-snug font-medium" style={{ color: '#7A5405' }}>
          {areaMismatchSkipReason(mismatch)}
        </p>
      </div>
    );
  }

  if (v.state === 'NOT_FARMLAND') {
    const cls = landCoverLabel(farm.land_cover?.class);
    return (
      <div className="mt-1 space-y-0.5">
        <p className="text-[11px] leading-snug" style={{ color: '#7A5405' }}>
          Excluded — observed as {cls.toLowerCase()}, not farmland.
          {v.reason ? ` ${v.reason}` : ''}
        </p>
        <p className="text-[11px] leading-snug font-medium" style={{ color: '#7A5405' }}>
          Skipped — we do not score non-agricultural land.
        </p>
      </div>
    );
  }

  if (v.state === 'UNOBSERVED') {
    if (farm.parcel_viability?.outcome === 'not_viable') {
      const { detail, skip } = monitoringAreaTooSmallMessages(farm, measured);
      return (
        <div className="mt-1 space-y-0.5">
          <p className="text-[11px] leading-snug" style={{ color: '#7A5405' }}>
            {detail}
          </p>
          <p className="text-[11px] leading-snug font-medium" style={{ color: '#7A5405' }}>
            {skip}
          </p>
        </div>
      );
    }
    const ds = farm.data_sufficiency;
    const detail =
      ds?.observed_fraction != null
        ? ` Clear on ${Math.round(ds.observed_fraction * 100)}% of the window.`
        : '';
    return (
      <div className="mt-1 space-y-0.5">
        <p className="text-[11px] leading-snug" style={{ color: '#7A5405' }}>
          {ds?.reason || `Too few clear satellite views of this plot.${detail}`}
        </p>
        <p className="text-[11px] leading-snug font-medium" style={{ color: '#7A5405' }}>
          Skipped — insufficient observation to score reliably.
        </p>
      </div>
    );
  }

  if (v.state === 'FAILED') {
    return (
      <div className="mt-1 space-y-0.5">
        <p className="text-[11px] leading-snug" style={{ color: '#9A2E1F' }}>
          {v.reason || 'The analysis did not complete.'}
        </p>
        <p className="text-[11px] leading-snug font-medium" style={{ color: '#9A2E1F' }}>
          Failed — re-run assessment or check pipeline logs.
        </p>
      </div>
    );
  }

  return (
    <p className="text-[11px] text-ink-muted mt-1 leading-snug">{farm.skipped_reason}</p>
  );
}

function AreaLine({
  registeredHa,
  measuredHa,
  geometry,
  viability,
}: {
  registeredHa?: number;
  measuredHa?: number | null;
  geometry?: unknown;
  viability?: FarmAssessment['parcel_viability'];
}) {
  const fromGeom = measuredHa ?? geometryAreaHa(geometry);
  const mismatch = areaMismatchOf({
    registeredHa,
    measuredHa: fromGeom,
    viability,
  });
  if (mismatch) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded border bg-amber-50 border-amber-200 text-amber-950">
          AgriStack {formatHa(mismatch.registeredHa)}
        </span>
        <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded border bg-amber-50 border-amber-200 text-amber-950">
          Mapped {formatHa(mismatch.measuredHa)}
        </span>
      </span>
    );
  }
  if (registeredHa != null) return <span>{formatHa(Number(registeredHa))}</span>;
  if (fromGeom != null) return <span>{formatHa(fromGeom)}</span>;
  return null;
}

function statusBadge(status: StreamRowStatus) {
  switch (status) {
    case 'pending':
      return { label: 'Pending', className: 'bg-stone-100 text-stone-600 border-stone-200' };
    case 'analyzing':
      return { label: 'Analyzing…', className: 'bg-sky-50 text-sky-800 border-sky-200' };
    case 'scored':
      return { label: 'Scored', className: 'bg-emerald-50 text-emerald-800 border-emerald-200' };
    case 'skipped':
      return { label: 'Skipped', className: 'bg-amber-50 text-amber-900 border-amber-200' };
    case 'failed':
      return { label: 'Failed', className: 'bg-red-50 text-red-700 border-red-200' };
  }
}

export function StreamingFarmList({
  rows,
  jobId,
  farmerId,
  doneCount,
  totalCount,
  className = '',
  selectedPlotKey = null,
  onSelectPlot,
}: {
  rows: StreamFarmRow[];
  jobId: string | null;
  farmerId: string;
  doneCount: number;
  totalCount: number;
  className?: string;
  selectedPlotKey?: string | null;
  onSelectPlot?: (plotKey: string) => void;
}) {
  return (
    <div
      className={`bg-white rounded-xl border border-rule overflow-hidden flex flex-col ${className}`}
    >
      <div className="px-5 py-4 border-b border-rule flex items-center justify-between gap-3 flex-wrap shrink-0">
        <div>
          <h3 className="text-sm font-bold text-stone-900 uppercase tracking-wider">
            Farms
          </h3>
          <p className="text-xs text-stone-500 mt-0.5">
            Click a farm to focus it on the map. Open Details for plot insights.
          </p>
        </div>
        <p className="text-sm font-mono text-stone-600">
          {doneCount}/{totalCount || rows.length} done
        </p>
      </div>

      <ul className="divide-y divide-rule overflow-y-auto flex-1 min-h-0">
        {rows.map((row) => {
          const badge = statusBadge(row.status);
          const a = row.assessment;
          const key = plotKeyOf(row);
          const selected = selectedPlotKey === key;
          const detailHref = `/dashboard/farm/${encodeURIComponent(key)}?farmer_id=${encodeURIComponent(farmerId)}${
            jobId ? `&job_id=${encodeURIComponent(jobId)}` : ''
          }`;
          const canOpenDetails =
            row.status === 'scored' ||
            row.status === 'skipped' ||
            row.status === 'failed';

          return (
            <li
              key={key}
              role="button"
              tabIndex={0}
              onClick={() => onSelectPlot?.(key)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelectPlot?.(key);
                }
              }}
              className={`px-5 py-3.5 flex items-start gap-4 cursor-pointer transition-colors ${
                selected
                  ? 'bg-amber-50/80 border-l-4 border-l-amber-400'
                  : 'hover:bg-paper/70 border-l-4 border-l-transparent'
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-stone-900 font-mono truncate">
                  {row.farm_id || key}
                </p>
                <p className="text-xs text-stone-500 mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1">
                  <span>{tenureLabel(row)}</span>
                  <AreaLine
                    registeredHa={row.area_ha}
                    measuredHa={row.measured_area_ha ?? row.assessment?.measured_area_ha}
                    geometry={row.geometry}
                    viability={row.assessment?.parcel_viability}
                  />
                  {row.crop ? <span>· {row.crop}</span> : null}
                </p>
                {a && (row.status === 'skipped' || row.status === 'failed') && (
                  <ExclusionNote
                    farm={a}
                    registeredHa={row.area_ha}
                    measuredHa={row.measured_area_ha ?? a.measured_area_ha}
                    geometry={row.geometry}
                  />
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0 self-center">
                <span
                  className={`inline-flex px-2 py-0.5 rounded-md text-[11px] font-medium border ${badge.className}`}
                >
                  {badge.label}
                </span>
                {row.status === 'scored' && a?.index_score != null && (
                  <>
                    {a.risk_category && (
                      <span
                        className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-bold border"
                        style={bandChipStyle(bandForRiskCategory(a.risk_category))}
                      >
                        {a.risk_category}
                      </span>
                    )}
                    <span className="text-sm font-bold text-stone-800 font-mono">
                      {formatScoreWhole(a.index_score)}
                    </span>
                  </>
                )}
              </div>

              <div className="shrink-0 self-center">
                {canOpenDetails ? (
                  <Link
                    href={detailHref}
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center justify-center min-w-[5.5rem] px-4 py-2 text-sm font-bold rounded-lg border-2 border-emerald-600 bg-emerald-600 text-white shadow-sm hover:bg-emerald-500 hover:border-emerald-500 transition-colors"
                  >
                    Details
                  </Link>
                ) : (
                  <span className="inline-flex items-center justify-center min-w-[5.5rem] px-4 py-2 text-sm font-semibold rounded-lg border border-stone-200 bg-stone-100 text-stone-400 cursor-not-allowed">
                    Details
                  </span>
                )}
              </div>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="px-5 py-8 text-center text-sm text-stone-500">
            No plots found for this farmer.
          </li>
        )}
      </ul>
    </div>
  );
}
