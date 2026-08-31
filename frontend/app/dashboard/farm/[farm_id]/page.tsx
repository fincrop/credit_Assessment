'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import type { AssessmentPayload, FarmAssessment } from '../../../types/assessment';
import { pollJobStatusSafe } from '../../../lib/assessmentClient';
import { plotKeyOf, assignPlotKeysClient } from '../../../lib/plotKey';
import { rowStatusFromAssessment } from '../../../lib/streamFarms';
import { PlotBoundaryMap, measuredFootprintOf } from '../../components/PlotBoundaryMap';
import { FarmKbsPanel } from '../../components/FarmKbsPanel';
import { FarmSlimFallbackCard } from '../../components/IndexInsightsCard';
import { RefusalPanel } from '../../components/RefusalPanel';
import { NdviTrajectory } from '../../components/NdviTrajectory';
import { OverviewFindingsPanel } from '../../components/OverviewFindingsPanel';
import { CropPerformanceSummary } from '../../components/CropPerformanceSummary';
import { useReport } from '../../../lib/useReport';
import { terminalStateOfFarm } from '../../../lib/terminalState';
import { WeatherSection } from '../../components/WeatherSection';
import { AIEnrichmentSection } from '../../components/AIEnrichmentSection';
import { useRiskView } from '../../../lib/useRiskView';
import {
  buildFarmPlotPayload,
  plotHasAnalysisDetail,
} from '../../../lib/farmPlotPayload';
import {
  areaMismatchOf,
  areaMismatchSkipNote,
  formatHa,
  geometryAreaHa,
} from '../../../lib/areaMismatch';
import {
  isMonitoringAreaTooSmall,
  monitoringAreaTooSmallMessages,
} from '../../../lib/plotSkipMessage';

type TabId = 'overview' | 'cropPerf' | 'weather' | 'ai';

const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'cropPerf', label: 'Crops & seasons' },
  { id: 'weather', label: 'Weather' },
  { id: 'ai', label: 'Explainability' },
];

export default function FarmDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-paper flex items-center justify-center text-sm text-stone-500">
          Loading farm…
        </div>
      }
    >
      <FarmDetailContent />
    </Suspense>
  );
}

function FarmDetailContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const plotKey = decodeURIComponent(String(params.farm_id || ''));
  const farmerId = (searchParams.get('farmer_id') || '').trim();
  const jobId = (searchParams.get('job_id') || '').trim();

  const [data, setData] = useState<AssessmentPayload | null>(null);
  const [farmGeom, setFarmGeom] = useState<{
    geometry?: { type?: string; coordinates?: unknown } | null;
    centroid?: { lat: number; lng: number } | null;
    farm_name?: string;
    area_ha?: number;
  } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        let payload: AssessmentPayload | null = null;

        if (jobId && /^[a-f0-9]{24}$/i.test(jobId)) {
          try {
            const polled = await pollJobStatusSafe(jobId);
            if (polled.ok && polled.job.result) payload = polled.job.result;
          } catch {
            /* fall through */
          }
        }

        if (!payload && farmerId) {
          const res = await fetch(
            `/api/assessments/latest?farmer_id=${encodeURIComponent(farmerId)}`,
            { credentials: 'include' }
          );
          const json = await res.json();
          if (res.ok && json.assessment) {
            payload = json.assessment as AssessmentPayload;
          }
        }

        if (!payload) {
          if (!cancelled) {
            setError('unavailable');
            setData(null);
          }
          return;
        }

        if (!cancelled) setData(payload);

        const fid = farmerId || String(payload.farmer_id || '');
        if (fid) {
          const fiRes = await fetch(`/api/farm-info/${encodeURIComponent(fid)}`, {
            credentials: 'include',
          });
          const fiJson = await fiRes.json();
          if (fiRes.ok && Array.isArray(fiJson.farm_info?.farms)) {
            const farms = assignPlotKeysClient(fiJson.farm_info.farms);
            const match = farms.find(
              (f) => plotKeyOf(f as { plot_key?: string; farm_id?: string }) === plotKey
            );
            if (match && !cancelled) {
              const c = match.centroid as { lat?: number; lng?: number } | undefined;
              setFarmGeom({
                geometry: (match.geometry || match.boundary) as {
                  type?: string;
                  coordinates?: unknown;
                } | null,
                centroid:
                  c?.lat != null && c?.lng != null
                    ? { lat: Number(c.lat), lng: Number(c.lng) }
                    : null,
                farm_name: String(match.farm_name || match.farm_id || plotKey),
                area_ha: typeof match.area_ha === 'number' ? match.area_ha : undefined,
              });
            }
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [plotKey, farmerId, jobId]);

  const farmRow: FarmAssessment | null = useMemo(() => {
    if (!data?.farm_assessments) return null;
    return data.farm_assessments.find((f) => plotKeyOf(f) === plotKey) || null;
  }, [data, plotKey]);

  const rowStatus = rowStatusFromAssessment(farmRow);
  const hasDetail = plotHasAnalysisDetail(farmRow);

  const showWeather =
    hasDetail ||
    data?.farmer_level?.weather_shared === true ||
    (data?.farmer_level?.weather_shared == null &&
      (data?.farmer_level?.diversification?.n_districts ?? 1) <= 1);

  const backHref = `/dashboard?farmer_id=${encodeURIComponent(
    farmerId || String(data?.farmer_id || '')
  )}${jobId ? `&job_id=${encodeURIComponent(jobId)}` : ''}`;

  const plotPayload = useMemo(() => {
    if (!data) return null;
    return buildFarmPlotPayload(data, farmRow);
  }, [data, farmRow]);

  const farmView = useRiskView(plotPayload);

  // Per-plot refusals arrive as slim records on the multi-farm result, so this
  // resolves from the farm row rather than from a full payload.
  const plotRefusal = useMemo(() => terminalStateOfFarm(farmRow), [farmRow]);

  const { report } = useReport(farmerId || String(data?.farmer_id || ''), {
    plotKey,
    enabled: !!data,
  });

  if (loading) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-sm text-stone-500">
        Loading farm details…
      </div>
    );
  }

  if (error === 'unavailable' || !data || !plotPayload) {
    return (
      <div className="min-h-screen bg-paper p-6">
        <div className="max-w-lg mx-auto mt-20 bg-white border border-rule rounded-xl p-8 text-center">
          <h1 className="text-lg font-bold text-stone-900 mb-2">Assessment unavailable</h1>
          <p className="text-sm text-stone-500 mb-6">
            This job may have expired or was not found. Re-run the assessment for this farmer.
          </p>
          <Link
            href={`/dashboard?farmer_id=${encodeURIComponent(farmerId || '')}&mode=assess`}
            className="inline-flex bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-4 py-2 rounded-lg text-sm"
          >
            Re-run assessment
          </Link>
        </div>
      </div>
    );
  }

  const scored = rowStatus === 'scored' && farmRow?.index_score != null;
  const registeredHa = farmGeom?.area_ha ?? farmRow?.area_ha;
  const measuredHa =
    farmRow?.measured_area_ha ??
    geometryAreaHa(farmGeom?.geometry) ??
    plotPayload?.parcel_viability?.evidence?.geometry_ha;
  const areaMismatch = areaMismatchOf({
    registeredHa,
    measuredHa,
    viability: plotPayload?.parcel_viability ?? farmRow?.parcel_viability,
  });
  const tooSmallNote =
    farmRow && isMonitoringAreaTooSmall(farmRow, measuredHa)
      ? monitoringAreaTooSmallMessages(farmRow, measuredHa)
      : null;

  return (
    <div className="min-h-screen bg-paper text-stone-800">
      <header className="bg-white border-b border-rule sticky top-0 z-20">
        <div className="page-shell h-14 flex items-center justify-between">
          <Link href={backHref} className="text-sm text-stone-500 hover:text-emerald-700">
            ← Assessment overview
          </Link>
          <div className="flex items-center gap-3 min-w-0">
            {(farmerId || data?.farmer_id) && (
              <Link
                href={`/report/${encodeURIComponent(farmerId || String(data?.farmer_id || ''))}?plot_key=${encodeURIComponent(plotKey)}`}
                className="text-[12px] font-semibold text-ink-2 border border-rule rounded-md px-2.5 py-1.5 hover:bg-paper transition-colors shrink-0"
              >
                Open report
              </Link>
            )}
            <p className="text-xs font-mono text-ink-muted truncate max-w-[40%]">{plotKey}</p>
          </div>
        </div>
      </header>

      <main className="page-shell py-6 space-y-5">
        {/* Hero: farm identity + map | KBS */}
        <div className="grid lg:grid-cols-[38%_1fr] gap-4 items-stretch">
          <div className="bg-white rounded-xl border border-rule p-4 flex flex-col">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
              Farm
            </p>
            <h1 className="text-lg font-bold text-stone-900 mt-0.5 font-mono truncate">
              {farmGeom?.farm_name || farmRow?.farm_id || plotKey}
            </h1>
            <p className="text-xs text-stone-500 mt-1 flex flex-wrap items-center gap-1.5">
              {areaMismatch ? (
                <>
                  <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded border bg-amber-50 border-amber-200 text-amber-950">
                    AgriStack {formatHa(areaMismatch.registeredHa)}
                  </span>
                  <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded border bg-amber-50 border-amber-200 text-amber-950">
                    Mapped {formatHa(areaMismatch.measuredHa)}
                  </span>
                </>
              ) : registeredHa != null ? (
                <span>{formatHa(Number(registeredHa))}</span>
              ) : measuredHa != null ? (
                <span>{formatHa(Number(measuredHa))}</span>
              ) : (
                <span>—</span>
              )}
              {farmRow?.crop ? <span>· {farmRow.crop}</span> : null}
              {farmRow?.is_ror_owner === false ? (
                <span>· Leased / joint</span>
              ) : farmRow?.is_ror_owner === true ? (
                <span>· Owned</span>
              ) : null}
            </p>
            {tooSmallNote ? (
              <p className="text-[11px] text-amber-900 mt-1.5 leading-snug">
                {tooSmallNote.detail} {tooSmallNote.skip}
              </p>
            ) : areaMismatch ? (
              <p className="text-[11px] text-amber-900 mt-1.5 leading-snug">
                {areaMismatchSkipNote(areaMismatch)}
              </p>
            ) : null}
            <div className="mt-3 flex-1 min-h-[220px]">
              <PlotBoundaryMap
                geometry={farmGeom?.geometry}
                centroid={farmGeom?.centroid}
                label={String(farmGeom?.farm_name || plotKey)}
                minHeight={220}
                measuredFootprint={measuredFootprintOf(
                  plotPayload?.risk_assessment?.footprint,
                  plotPayload?.geospatial_prep
                )}
                ndvi={
                  report?.ndvi_trajectory
                    ? {
                        dates: report.ndvi_trajectory.dates,
                        values: report.ndvi_trajectory.ndvi,
                      }
                    : null
                }
              />
            </div>
          </div>

          {/* A plot excluded as non-farmland or unobservable gets the refusal
              screen, not a KBS panel with an apologetic fallback string. The
              two are different findings and read differently to a lender. */}
          {plotRefusal.state === 'NOT_FARMLAND' || plotRefusal.state === 'UNOBSERVED' ? (
            <RefusalPanel verdict={plotRefusal} />
          ) : (
            <div className="space-y-2.5">
              <FarmKbsPanel
                view={scored ? farmView : null}
                fallbackMessage={
                  !farmRow
                    ? 'This plot has no row in the assessment result.'
                    : farmRow.skipped_reason
                      ? String(farmRow.skipped_reason).replace(/^error:/, 'Error: ')
                      : 'This plot was not scored in this run.'
                }
              />
            </div>
          )}
        </div>

        {!hasDetail && farmRow && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
            This assessment was saved without full plot detail. Overview still uses plot scores;
            Crop / Weather / Cycles need a <strong>re-run</strong> to populate enhanced panels.
          </div>
        )}

        <div className="flex bg-white border border-rule rounded-lg p-1 gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setActiveTab(t.id)}
              className={`px-4 py-2 text-sm font-medium rounded-md whitespace-nowrap transition-colors ${
                activeTab === t.id
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'text-stone-500 hover:text-stone-800 hover:bg-paper'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {activeTab === 'overview' && (
          <div className="space-y-4">
            <div className="grid lg:grid-cols-[3fr_2fr] gap-4 items-stretch min-h-[420px]">
              <NdviTrajectory
                variant="overview"
                trajectory={report?.ndvi_trajectory}
                cycles={plotPayload?.crop_cycles?.cycles}
                windowLabel={
                  plotPayload?.continuous_data_stats?.date_range?.start &&
                  plotPayload?.continuous_data_stats?.date_range?.end
                    ? `${plotPayload.continuous_data_stats.date_range.start} → ${plotPayload.continuous_data_stats.date_range.end}`
                    : undefined
                }
              />
              <OverviewFindingsPanel
                view={scored ? farmView : null}
                landCover={plotPayload?.land_cover ?? report?.land_cover}
                scored={scored}
              />
            </div>
            {farmRow && !hasDetail && <FarmSlimFallbackCard farm={farmRow} />}
          </div>
        )}

        {activeTab === 'cropPerf' && (
          <div>
            {hasDetail ||
            plotPayload.cropping_analysis ||
            plotPayload.performance_analysis ||
            plotPayload.crop_cycles ||
            plotPayload.continuous_data_stats ||
            report?.ndvi_trajectory ? (
              <CropPerformanceSummary
                data={plotPayload}
                ndviTrajectory={report?.ndvi_trajectory}
              />
            ) : farmRow ? (
              <FarmSlimFallbackCard farm={farmRow} />
            ) : (
              <EmptyTab
                title="Crops & seasons"
                body="No crop or season analysis is available for this plot."
              />
            )}
          </div>
        )}

        {activeTab === 'weather' &&
          (showWeather ? (
            plotPayload.weather_analysis ? (
              <div className="space-y-2">
                {!hasDetail && data.weather_analysis && (
                  <p className="text-xs text-stone-500">
                    Showing holding-area weather (shared across plots in this assessment).
                  </p>
                )}
                <WeatherSection data={plotPayload} ndviTrajectory={report?.ndvi_trajectory} />
              </div>
            ) : farmRow ? (
              <div className="space-y-4">
                <WeatherSubIndexFallback farm={farmRow} />
                {!hasDetail && <FarmSlimFallbackCard farm={farmRow} />}
              </div>
            ) : (
              <EmptyTab title="Weather" body="No weather analysis in this payload." />
            )
          ) : (
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-950">
              Weather is hidden for this farm — plots span multiple districts.{' '}
              <Link href={backHref} className="font-semibold underline">
                Back to assessment overview
              </Link>
            </div>
          ))}

        {activeTab === 'ai' && <AIEnrichmentSection data={plotPayload} />}
      </main>
    </div>
  );
}

function EmptyTab({ title, body }: { title: string; body: string }) {
  return (
    <div className="bg-white rounded-xl border border-rule p-6">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-2">
        {title}
      </h2>
      <p className="text-sm text-ink-muted">{body}</p>
    </div>
  );
}

function WeatherSubIndexFallback({ farm }: { farm: FarmAssessment }) {
  const w = farm.sub_indices?.weather;
  return (
    <div className="bg-white rounded-xl border border-rule p-5 space-y-3">
      <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
        Weather resilience
      </h2>
      <p className="text-xs text-stone-500">
        Detailed weather intervals are not in this saved job. Plot weather sub-index from scoring:
      </p>
      {w != null ? (
        <div className="rounded-lg border border-rule bg-paper/50 p-4 max-w-xs">
          <p className="text-[10px] text-ink-muted uppercase font-semibold">Weather sub-index</p>
          <p className="text-2xl font-bold font-mono text-stone-900 mt-1">{Number(w).toFixed(1)}</p>
          <p className="text-[11px] text-stone-500 mt-1">Scale 0–100 (pillar score)</p>
        </div>
      ) : (
        <p className="text-sm text-ink-muted">No weather sub-index on this plot row.</p>
      )}
    </div>
  );
}
