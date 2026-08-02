'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import type { AssessmentPayload, FarmAssessment } from '../../../types/assessment';
import { pollJobStatusSafe } from '../../../lib/assessmentClient';
import { plotKeyOf, assignPlotKeysClient } from '../../../lib/plotKey';
import { rowStatusFromAssessment } from '../../../lib/streamFarms';
import { formatScoreWhole } from '../../../lib/formatRisk';
import { riskBgClass } from '../../../lib/format';
import { PlotBoundaryMap } from '../../components/PlotBoundaryMap';
import { SubIndexBars } from '../../components/SubIndexBars';
import { CropCyclesSection } from '../../components/CropCyclesSection';
import { PerformanceSection } from '../../components/PerformanceSection';
import { WeatherSection } from '../../components/WeatherSection';
import { AIEnrichmentSection } from '../../components/AIEnrichmentSection';
import { useRiskView } from '../../../lib/useRiskView';
import type { ReasonCode } from '../../../types/assessment';

export default function FarmDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#F5F2EB] flex items-center justify-center text-sm text-stone-500">
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
            /* fall through to history */
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
            const match = farms.find((f) => plotKeyOf(f as { plot_key?: string; farm_id?: string }) === plotKey);
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
    return (
      data.farm_assessments.find((f) => plotKeyOf(f) === plotKey) || null
    );
  }, [data, plotKey]);

  const rowStatus = rowStatusFromAssessment(farmRow);
  const showWeather =
    data?.farmer_level?.weather_shared === true ||
    (data?.farmer_level?.weather_shared == null &&
      (data?.farmer_level?.diversification?.n_districts ?? 1) <= 1);

  const backHref = `/dashboard?farmer_id=${encodeURIComponent(
    farmerId || String(data?.farmer_id || '')
  )}${jobId ? `&job_id=${encodeURIComponent(jobId)}` : ''}`;

  // Synthetic payload for SubIndexBars on farm score
  const farmViewPayload = useMemo(() => {
    if (!farmRow || farmRow.index_score == null) return null;
    return {
      farmer_id: data?.farmer_id,
      farmer_level: {
        index_score: farmRow.index_score,
        raw_index: farmRow.raw_index,
        risk_category: (farmRow.risk_category || 'MEDIUM') as never,
        confidence_gate: farmRow.confidence_gate ?? null,
        sub_indices: farmRow.sub_indices || {},
        weights: {},
        reason_codes: farmRow.reason_codes,
      },
    } as AssessmentPayload;
  }, [farmRow, data]);

  const farmView = useRiskView(farmViewPayload);

  if (loading) {
    return (
      <div className="min-h-screen bg-[#F5F2EB] flex items-center justify-center text-sm text-stone-500">
        Loading farm details…
      </div>
    );
  }

  if (error === 'unavailable' || !data) {
    return (
      <div className="min-h-screen bg-[#F5F2EB] p-6">
        <div className="max-w-lg mx-auto mt-20 bg-white border border-[#E4DFD4] rounded-xl p-8 text-center">
          <h1 className="text-lg font-bold text-stone-900 mb-2">Assessment unavailable</h1>
          <p className="text-sm text-stone-500 mb-6">
            This job may have expired or was not found. Re-run the assessment for this farmer.
          </p>
          <Link
            href={`/dashboard?farmer_id=${encodeURIComponent(farmerId || '')}`}
            className="inline-flex bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-4 py-2 rounded-lg text-sm"
          >
            Re-run assessment
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F5F2EB] text-stone-800">
      <header className="bg-white border-b border-[#E4DFD4] sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link href={backHref} className="text-sm text-stone-500 hover:text-emerald-700">
            ← Assessment overview
          </Link>
          <p className="text-xs font-mono text-stone-400 truncate max-w-[40%]">{plotKey}</p>
        </div>
      </header>

      <main className="max-w-7xl mx-auto p-6 space-y-6">
        <div className="grid lg:grid-cols-2 gap-5">
          <div className="bg-white rounded-xl border border-[#E4DFD4] p-5">
            <p className="text-[10px] uppercase tracking-wider text-stone-400 font-semibold">Farm</p>
            <h1 className="text-xl font-bold text-stone-900 mt-1 font-mono">
              {farmGeom?.farm_name || farmRow?.farm_id || plotKey}
            </h1>
            <p className="text-sm text-stone-500 mt-2">
              {farmGeom?.area_ha != null
                ? `${farmGeom.area_ha.toFixed(2)} ha`
                : farmRow?.area_ha != null
                  ? `${farmRow.area_ha.toFixed(2)} ha`
                  : '—'}
              {farmRow?.crop ? ` · ${farmRow.crop}` : ''}
            </p>
          </div>

          <div className="bg-white rounded-xl border border-[#E4DFD4] p-5">
            {!farmRow ? (
              <div>
                <h2 className="text-sm font-bold text-stone-900 mb-2">Not in this assessment run</h2>
                <p className="text-sm text-stone-500">
                  This plot exists in farm records but has no row in the assessment result.
                </p>
              </div>
            ) : rowStatus === 'scored' && farmRow.index_score != null ? (
              <div>
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-3xl font-bold text-stone-900 font-mono">
                    {formatScoreWhole(farmRow.index_score)}
                  </span>
                  {farmRow.risk_category && (
                    <span
                      className={`inline-flex px-2.5 py-1 rounded-full text-xs font-bold border ${riskBgClass(
                        farmRow.risk_category
                      )}`}
                    >
                      {farmRow.risk_category}
                    </span>
                  )}
                </div>
                {farmViewPayload && (
                  <SubIndexBars view={farmView} hideGate />
                )}
                {(farmRow.reason_codes?.length ?? 0) > 0 && (
                  <ul className="mt-3 space-y-1">
                    {farmRow.reason_codes!.slice(0, 4).map((r: ReasonCode, i: number) => (
                      <li key={i} className="text-xs text-stone-600">
                        • {r.message || r.code}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <div>
                <h2 className="text-sm font-bold text-amber-900 mb-2">
                  Not scored
                  {rowStatus === 'failed' ? ' (failed)' : ''}
                </h2>
                <p className="text-sm text-stone-600">
                  {farmRow.skipped_reason
                    ? String(farmRow.skipped_reason).replace(/^error:/, 'Error: ')
                    : 'This plot was not included in the scored aggregate.'}
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="grid lg:grid-cols-2 gap-5">
          <div>
            <h2 className="text-sm font-bold text-stone-700 mb-2 uppercase tracking-wider">
              Satellite map
            </h2>
            <PlotBoundaryMap
              geometry={farmGeom?.geometry}
              centroid={farmGeom?.centroid}
              label={String(farmGeom?.farm_name || plotKey)}
            />
          </div>
          <div className="space-y-4">
            <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900">
              {showWeather
                ? 'Farmer-level context (representative of this area).'
                : 'Weather is hidden for this farm — plots span multiple districts. Open the farmer overview for portfolio weather.'}
            </div>
            <CropCyclesSection data={data} />
            <PerformanceSection data={data} />
          </div>
        </div>

        {showWeather ? (
          <div>
            <p className="text-xs text-stone-500 mb-2">
              Farmer-level context (representative of this area).
            </p>
            <WeatherSection data={data} />
          </div>
        ) : (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-950">
            Weather not shown on this farm page because <code className="font-mono">weather_shared</code> is
            false (dispersed districts).{' '}
            <Link href={backHref} className="font-semibold underline">
              View farmer overview
            </Link>
          </div>
        )}

        <div>
          <p className="text-xs text-stone-500 mb-2">
            Explainability is farmer-level (not plot-specific).
          </p>
          <AIEnrichmentSection data={data} />
        </div>
      </main>
    </div>
  );
}
