'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../components/providers/AuthProvider';
import { AoiMap } from '../classification/components/AoiMap';
import { AoiUpload } from '../classification/components/AoiUpload';
import { CLASSIFIABLE_CROPS, SEASONS, formatHa, type AreaOfInterest } from '../classification/types';
import { DownloadPanel } from './components/DownloadPanel';
import { ResultsPanel } from './components/ResultsPanel';
import {
  STAGE_LABELS,
  todayISO,
  type MonitorHistoryItem,
  type MonitorInputs,
  type MonitorProgress,
  type MonitorStage,
  type MonitoringResult,
} from './types';

const STEPS = [
  { id: 1, label: 'Field' },
  { id: 2, label: 'Crop' },
  { id: 3, label: 'Processing' },
  { id: 4, label: 'Results' },
];

function StepBar({ current }: { current: number }) {
  return (
    <ol className="flex items-center w-full gap-1 mb-2">
      {STEPS.map((step, idx) => {
        const done = current > step.id;
        const active = current === step.id;
        return (
          <li key={step.id} className="flex items-center flex-1 min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <span
                className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold border ${
                  done
                    ? 'bg-emerald-500 border-emerald-500 text-white'
                    : active
                      ? 'bg-emerald-500/20 border-emerald-500 text-emerald-700'
                      : 'bg-white border-rule text-stone-400'
                }`}
              >
                {done ? '✓' : step.id}
              </span>
              <span className={`text-[11px] sm:text-xs font-medium truncate ${active ? 'text-stone-900' : 'text-stone-500'}`}>
                {step.label}
              </span>
            </div>
            {idx < STEPS.length - 1 && (
              <div className={`flex-1 h-px mx-1.5 sm:mx-2 ${done ? 'bg-emerald-500/50' : 'bg-rule'}`} />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function classFieldId(feature: GeoJSON.Feature, index: number): string {
  const props = feature.properties as { field_id?: string } | null;
  return String(props?.field_id ?? index);
}

function toClassArea(feature: GeoJSON.Feature, index: number): AreaOfInterest {
  const props = (feature.properties || {}) as Record<string, unknown>;
  const crop = String(props.crop || 'Field');
  const ha = typeof props.area_ha === 'number' ? props.area_ha : 0;
  return {
    aoi_id: classFieldId(feature, index),
    name: `${crop} · ${ha.toFixed(2)} ha`,
    source: 'uploaded',
    boundary: feature.geometry as GeoJSON.Polygon | GeoJSON.MultiPolygon,
    area_ha: ha,
    centroid: { lat: 0, lng: 0 },
    color: typeof props.color === 'string' ? props.color : undefined,
  };
}

const EMPTY: MonitorInputs = {
  name: '',
  crop: 'Soyabean',
  confidence: 0.8,
  season: 'kharif',
  as_of: todayISO(),
  sowing_date: '',
  district_yield_t_ha: '',
  cluster_id: '',
  village: '',
  classification_job_id: '',
  source_field_id: '',
};

export default function MonitoringPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [areas, setAreas] = useState<AreaOfInterest[]>([]);
  const [inputs, setInputs] = useState<MonitorInputs>(EMPTY);
  const [progress, setProgress] = useState<MonitorProgress | null>(null);
  const [result, setResult] = useState<MonitoringResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [history, setHistory] = useState<MonitorHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [classJobs, setClassJobs] = useState<Array<{ job_id: string; label: string; season?: string }>>([]);
  const [classJobId, setClassJobId] = useState('');
  const [classFields, setClassFields] = useState<GeoJSON.Feature[]>([]);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openedFromUrl = useRef(false);

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [loading, user, router]);

  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/monitoring/history', { credentials: 'include' });
      const data = await res.json();
      const jobs = (Array.isArray(data.jobs) ? data.jobs : []) as MonitorHistoryItem[];
      setHistory(jobs);
      return jobs;
    } catch {
      setHistory([]);
      return [] as MonitorHistoryItem[];
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const poll = useCallback(async (jobId: string) => {
    try {
      const res = await fetch(`/api/monitoring/status/${jobId}`, { credentials: 'include' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Status check failed');
      setProgress(data);
      if (data.stage === 'complete') {
        const r = await fetch(`/api/monitoring/result/${jobId}`, { credentials: 'include' });
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.error || 'Could not load the result');
        setResult(payload as MonitoringResult);
        setStep(4);
        void loadHistory();
        return;
      }
      if (data.stage === 'failed') {
        setError(data.error || 'Monitoring failed.');
        return;
      }
      pollRef.current = setTimeout(() => void poll(jobId), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lost contact with the job.');
    }
  }, [loadHistory]);

  const openJob = useCallback(async (item: MonitorHistoryItem) => {
    if (pollRef.current) clearTimeout(pollRef.current);
    setError(null);
    if (item.areas?.length) setAreas(item.areas as AreaOfInterest[]);
    setInputs({ ...EMPTY, ...item.inputs, name: item.name, crop: item.crop || item.inputs.crop || EMPTY.crop });
    window.history.replaceState(null, '', `/monitoring?job=${item.job_id}`);
    if (item.stage === 'complete') {
      const r = await fetch(`/api/monitoring/result/${item.job_id}`, { credentials: 'include' });
      const payload = await r.json();
      if (!r.ok) {
        setError(payload.error || 'Could not open that run.');
        return;
      }
      setResult(payload as MonitoringResult);
      setProgress({ job_id: item.job_id, stage: 'complete', percent: 100 });
      setStep(4);
      return;
    }
    setResult(null);
    setProgress({
      job_id: item.job_id,
      stage: (item.stage as MonitorStage) || 'queued',
      percent: null,
      error: item.error || undefined,
    });
    setStep(3);
    if (item.stage !== 'failed') pollRef.current = setTimeout(() => void poll(item.job_id), 400);
    else setError(item.error || 'Monitoring failed.');
  }, [poll]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    void (async () => {
      const jobs = await loadHistory();
      if (cancelled || openedFromUrl.current) return;
      const id = new URLSearchParams(window.location.search).get('job');
      if (!id) return;
      openedFromUrl.current = true;
      const found = jobs.find((item) => item.job_id === id);
      if (found) await openJob(found);
    })();
    void fetch('/api/classification/history', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        const jobs = Array.isArray(data.jobs) ? data.jobs : [];
        setClassJobs(
          jobs
            .filter((j: { stage?: string }) => j.stage === 'complete')
            .slice(0, 20)
            .map((j: { job_id: string; aoi_name?: string; season?: string; inputs?: { region_name?: string } }) => ({
              job_id: j.job_id,
              label: j.aoi_name || j.inputs?.region_name || j.job_id.slice(-6),
              season: j.season,
            }))
        );
      })
      .catch(() => setClassJobs([]));
    return () => {
      cancelled = true;
    };
  }, [user, loadHistory, openJob]);

  const useClassifiedField = async (jobId: string) => {
    setError(null);
    const res = await fetch(`/api/classification/result/${jobId}`, { credentials: 'include' });
    const payload = await res.json();
    if (!res.ok) {
      setError(payload.error || 'Could not load that classification.');
      return;
    }
    const features = (payload.fields?.features || []) as GeoJSON.Feature[];
    const named = features.filter((f) => {
      const geom = f.geometry;
      if (!geom || (geom.type !== 'Polygon' && geom.type !== 'MultiPolygon')) return false;
      const crop = String((f.properties as { crop?: string } | null)?.crop || '');
      return CLASSIFIABLE_CROPS.includes(crop as (typeof CLASSIFIABLE_CROPS)[number]);
    });
    setClassFields(named);
    setClassJobId(jobId);
    const picked = classJobs.find((job) => job.job_id === jobId);
    if (picked?.season && picked.season !== 'whole_year') {
      setInputs((prev) => ({ ...prev, season: picked.season || prev.season }));
    }
    if (named.length === 0) {
      setError('That classification has no named crop fields to monitor.');
    }
  };

  const selectClassified = (features: GeoJSON.Feature[]) => {
    const next = features.map((feature, index) => toClassArea(feature, index));
    setAreas(next);
    const crops = [...new Set(features.map((feature) => String((feature.properties as { crop?: string } | null)?.crop || '')))];
    const label = classJobs.find((job) => job.job_id === classJobId)?.label || 'Classification';
    setInputs((prev) => ({
      ...prev,
      name: features.length > 1 ? label : (next[0]?.name || prev.name),
      crop: crops.length === 1 ? crops[0] : prev.crop,
      classification_job_id: classJobId,
      source_field_id: features.length === 1 ? next[0]?.aoi_id || '' : '',
      village: prev.village || label,
      confidence: features.length === 1 && typeof features[0].properties?.confidence === 'number'
        ? Number(features[0].properties.confidence)
        : prev.confidence,
    }));
  };

  const start = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const classIds = new Set(classFields.map((feature, index) => classFieldId(feature, index)));
      const ids = areas.map((area) => area.aoi_id);
      const fromClass = Boolean(inputs.classification_job_id) && ids.length > 0 && ids.every((id) => classIds.has(id));
      const res = await fetch('/api/monitoring/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(
          fromClass
            ? { classification_job_id: inputs.classification_job_id, field_ids: ids, inputs }
            : { areas: areas.slice(0, 1), inputs },
        ),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Could not start monitoring');
      setResult(null);
      window.history.replaceState(null, '', `/monitoring?job=${data.job_id}`);
      setStep(3);
      setProgress({ job_id: data.job_id, stage: data.stage || 'queued', percent: null });
      if (data.stage === 'failed') {
        setError('The monitoring service did not accept the job. Check that the API is running.');
        return;
      }
      void loadHistory();
      pollRef.current = setTimeout(() => void poll(data.job_id), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start monitoring');
    } finally {
      setSubmitting(false);
    }
  };

  const reset = () => {
    if (pollRef.current) clearTimeout(pollRef.current);
    setStep(1);
    setAreas([]);
    setInputs({ ...EMPTY, as_of: todayISO() });
    setProgress(null);
    setResult(null);
    setError(null);
    setClassFields([]);
    window.history.replaceState(null, '', '/monitoring');
    void loadHistory();
  };

  if (loading || !user) {
    return <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">Loading…</div>;
  }

  const field = areas[0];
  const totalHa = areas.reduce((sum, area) => sum + (Number(area.area_ha) || 0), 0);
  const many = areas.length > 1;
  const cropSummary = Object.entries(
    classFields.reduce<Record<string, number>>((counts, feature) => {
      const crop = String((feature.properties as { crop?: string } | null)?.crop || '');
      if (crop) counts[crop] = (counts[crop] || 0) + 1;
      return counts;
    }, {}),
  );

  return (
    <div className="h-[100dvh] flex flex-col bg-paper text-stone-800 overflow-hidden">
      <header className="page-shell flex items-center justify-between py-2 border-b border-rule flex-shrink-0">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-stone-500 hover:text-emerald-700 text-sm">← Home</Link>
          <div className="h-4 w-px bg-rule" />
          <div>
            <h1 className="text-sm font-bold text-stone-900 tracking-tight">Crop Monitoring</h1>
            <p className="text-[10px] text-stone-500 font-mono">Present-season condition, stress and yield</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/classification" className="text-xs text-stone-500 hover:text-emerald-700">
            Classification
          </Link>
          {step > 1 && (
            <button
              onClick={reset}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold px-3 py-1.5 hover:bg-emerald-700"
            >
              + New monitoring
            </button>
          )}
        </div>
      </header>

      <main className="page-shell flex-1 min-h-0 flex flex-col pt-2 pb-3">
        <StepBar current={step} />
        {error && (
          <div className="mb-2 rounded-xl border border-red-300 bg-red-50 px-3 py-2 flex-shrink-0">
            <p className="text-sm text-red-800">{error}</p>
          </div>
        )}
        <div className={`grid gap-3 items-stretch flex-1 min-h-0 ${step === 4 ? 'lg:grid-cols-[minmax(0,7fr)_minmax(280px,3fr)]' : 'lg:grid-cols-[1fr_400px]'}`}>
          <div className="flex flex-col min-w-0 min-h-0 lg:h-full">
            <AoiMap
              areas={areas}
              onAreasChange={(next) => setAreas(next.slice(-1))}
              resultLayer={step === 4 ? result?.fields ?? null : null}
              readOnly={step >= 3}
              heightClass="h-[52dvh] lg:flex-1 lg:min-h-0"
              toolbar={step === 4 && result ? <DownloadPanel result={result} /> : undefined}
            />
            {step < 3 && (
              <p className="text-[11px] text-stone-500 mt-1.5">
                Draw one field, upload its boundary, or monitor every farm from a finished classification.
                Mixed sowing inside a farm is split before stress is scored.
              </p>
            )}
          </div>

          <aside className="rounded-2xl border border-rule bg-white/70 backdrop-blur-sm p-4 min-w-0 min-h-0 lg:overflow-y-auto">
            {step === 1 && (
              <div className="space-y-5">
                <div>
                  <h2 className="text-base font-bold text-stone-900 mb-1">Choose the fields</h2>
                  <p className="text-xs text-stone-500 leading-relaxed">
                    Monitor every classified farm in one run, or pick a single farm.
                  </p>
                </div>
                <AoiUpload areas={areas} onAreasChange={(next) => setAreas(next.slice(-1))} />
                <div className="text-xs text-stone-500 flex justify-between">
                  <span>{areas.length ? `${areas.length} field${areas.length === 1 ? '' : 's'} selected` : 'No field yet'}</span>
                  <span className="font-semibold text-stone-800">{areas.length ? formatHa(totalHa) : '—'}</span>
                </div>
                <button
                  onClick={() => {
                    if (field && !inputs.name) setInputs({ ...inputs, name: field.name });
                    setStep(2);
                  }}
                  disabled={!areas.length}
                  className="w-full rounded-xl bg-emerald-600 text-white text-sm font-semibold py-2.5 hover:bg-emerald-700 disabled:opacity-40"
                >
                  Continue →
                </button>
                <div className="pt-3 border-t border-rule space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500">From a classification</h3>
                  <select
                    className="w-full rounded-lg border border-rule bg-white px-2 py-2 text-sm"
                    defaultValue=""
                    onChange={(e) => {
                      if (e.target.value) void useClassifiedField(e.target.value);
                    }}
                  >
                    <option value="">Select a completed classification…</option>
                    {classJobs.map((job) => (
                      <option key={job.job_id} value={job.job_id}>{job.label}</option>
                    ))}
                  </select>
                  {classFields.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs text-stone-600">
                        {classFields.length} classified farms
                        {cropSummary.length ? ` · ${cropSummary.map(([crop, count]) => `${crop} ${count}`).join(', ')}` : ''}
                      </p>
                      <button
                        type="button"
                        onClick={() => selectClassified(classFields)}
                        className="w-full rounded-lg bg-emerald-600 text-white text-sm font-semibold py-2 hover:bg-emerald-700"
                      >
                        Monitor all {classFields.length} fields
                      </button>
                      {cropSummary.length > 1 && cropSummary.map(([crop]) => (
                        <button
                          key={crop}
                          type="button"
                          onClick={() => selectClassified(classFields.filter((feature) => String((feature.properties as { crop?: string } | null)?.crop) === crop))}
                          className="w-full rounded-lg border border-rule text-sm py-2 hover:bg-emerald-50"
                        >
                          Monitor all {crop}
                        </button>
                      ))}
                      <p className="text-[11px] text-stone-500 leading-relaxed">
                        One run scores every selected farm. Satellite imagery is read once per crop for the whole set, so a village does not call Earth Engine once per polygon.
                      </p>
                      <ul className="max-h-36 overflow-y-auto space-y-1">
                        {classFields.slice(0, 12).map((feature, i) => {
                          const props = (feature.properties || {}) as Record<string, unknown>;
                          return (
                            <li key={classFieldId(feature, i)}>
                              <button
                                type="button"
                                onClick={() => selectClassified([feature])}
                                className="w-full text-left rounded-lg border border-rule px-2 py-1.5 text-xs hover:bg-emerald-50"
                              >
                                <span className="font-semibold">{String(props.crop || 'Field')}</span>
                                <span className="text-stone-500">
                                  {' '}· {typeof props.area_ha === 'number' ? `${props.area_ha.toFixed(2)} ha` : 'area unknown'}
                                </span>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                      {classFields.length > 12 && (
                        <p className="text-[11px] text-stone-500">Showing 12 of {classFields.length}. Use Monitor all for the rest.</p>
                      )}
                    </div>
                  )}
                </div>
                <div className="pt-3 border-t border-rule">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">Past monitoring</h3>
                  {historyLoading ? (
                    <p className="text-xs text-stone-500">Loading…</p>
                  ) : history.length === 0 ? (
                    <p className="text-xs text-stone-500">Completed runs will show up here.</p>
                  ) : (
                    <ul className="space-y-1">
                      {history.map((item) => (
                        <li key={item.job_id}>
                          <button
                            type="button"
                            onClick={() => void openJob(item)}
                            className="w-full text-left rounded-lg border border-rule px-2 py-1.5 hover:bg-emerald-50"
                          >
                            <span className="block text-xs font-semibold text-stone-900">{item.name} · {item.crop}</span>
                            <span className="block text-[11px] text-stone-500">
                              {STAGE_LABELS[item.stage as MonitorStage] || item.stage}
                              {item.zone_count ? ` · ${item.zone_count} zones` : ''}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}

            {step === 2 && (
              <div className="space-y-4">
                <div>
                  <h2 className="text-base font-bold text-stone-900 mb-1">Crop and season</h2>
                  <p className="text-xs text-stone-500">
                    {many
                      ? `${areas.length} farms keep the crop classification gave each of them. Satellite data is read once per crop, then every farm is scored.`
                      : 'The crop is the one classification assigned to this field. Its own duration and harvest season are used.'}
                  </p>
                </div>
                <label className="block text-xs text-stone-600">
                  Name
                  <input
                    value={inputs.name}
                    onChange={(e) => setInputs({ ...inputs, name: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                    placeholder="Field name"
                  />
                </label>
                {!many && (
                <label className="block text-xs text-stone-600">
                  Crop
                  <select
                    value={inputs.crop}
                    onChange={(e) => setInputs({ ...inputs, crop: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule bg-white px-2 py-2 text-sm"
                  >
                    {CLASSIFIABLE_CROPS.map((crop) => (
                      <option key={crop} value={crop}>{crop}</option>
                    ))}
                  </select>
                </label>
                )}
                {!many && (
                <label className="block text-xs text-stone-600">
                  Confidence ({Math.round(inputs.confidence * 100)}%)
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={inputs.confidence}
                    onChange={(e) => setInputs({ ...inputs, confidence: Number(e.target.value) })}
                    className="mt-1 w-full"
                  />
                </label>
                )}
                <label className="block text-xs text-stone-600">
                  Season
                  <select
                    value={inputs.season}
                    onChange={(e) => setInputs({ ...inputs, season: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule bg-white px-2 py-2 text-sm"
                  >
                    {SEASONS.filter((s) => s.value !== 'whole_year').map((s) => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs text-stone-600">
                  As of
                  <input
                    type="date"
                    value={inputs.as_of}
                    onChange={(e) => setInputs({ ...inputs, as_of: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                  />
                </label>
                {!many && (
                <label className="block text-xs text-stone-600">
                  Sowing date, if the farm record has one
                  <input
                    type="date"
                    value={inputs.sowing_date}
                    onChange={(e) => setInputs({ ...inputs, sowing_date: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                  />
                </label>
                )}
                <label className="block text-xs text-stone-600">
                  Cluster
                  <input
                    value={inputs.cluster_id}
                    onChange={(e) => setInputs({ ...inputs, cluster_id: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                    placeholder="Cluster of villages"
                  />
                </label>
                <label className="block text-xs text-stone-600">
                  Village
                  <input
                    value={inputs.village}
                    onChange={(e) => setInputs({ ...inputs, village: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                    placeholder="Village name"
                  />
                </label>
                <label className="block text-xs text-stone-600">
                  District yield (t/ha), optional
                  <input
                    value={inputs.district_yield_t_ha}
                    onChange={(e) => setInputs({ ...inputs, district_yield_t_ha: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-rule px-2 py-2 text-sm"
                    placeholder="1.2"
                  />
                </label>
                <div className="flex gap-2">
                  <button onClick={() => setStep(1)} className="rounded-xl border border-rule px-4 py-2.5 text-sm text-stone-600">← Back</button>
                  <button
                    onClick={() => void start()}
                    disabled={submitting || !inputs.crop}
                    className="flex-1 rounded-xl bg-emerald-600 text-white text-sm font-semibold py-2.5 hover:bg-emerald-700 disabled:opacity-60"
                  >
                    {submitting ? 'Starting…' : 'Run monitoring'}
                  </button>
                </div>
              </div>
            )}

            {step === 3 && progress && (
              <div className="space-y-4">
                <div>
                  <h2 className="text-base font-bold text-stone-900">Processing</h2>
                  <p className="text-xs text-stone-500 font-mono">Job {progress.job_id}</p>
                </div>
                <ol className="space-y-2">
                  {(['queued', 'observing', 'analysing', 'publishing', 'complete'] as MonitorStage[]).map((stage) => {
                    const order = ['queued', 'observing', 'analysing', 'publishing', 'complete'];
                    const active = order.indexOf(progress.stage);
                    const mine = order.indexOf(stage);
                    const done = progress.stage === 'failed' ? false : active > mine || progress.stage === 'complete';
                    const current = progress.stage === stage;
                    return (
                      <li key={stage} className={`rounded-lg border px-3 py-2 text-sm ${current ? 'border-emerald-400 bg-emerald-50' : 'border-rule'}`}>
                        <span className="font-medium">{done ? '✓ ' : current ? '◐ ' : '○ '}</span>
                        {STAGE_LABELS[stage]}
                        {current && progress.message ? <p className="text-[11px] text-stone-500 mt-0.5">{progress.message}</p> : null}
                      </li>
                    );
                  })}
                </ol>
                {typeof progress.percent === 'number' && (
                  <div className="h-1.5 rounded-full bg-stone-100 overflow-hidden">
                    <div className="h-full bg-emerald-500" style={{ width: `${progress.percent}%` }} />
                  </div>
                )}
              </div>
            )}

            {step === 4 && result && <ResultsPanel result={result} />}
          </aside>
        </div>
      </main>
    </div>
  );
}
