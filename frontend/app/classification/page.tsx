'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../components/providers/AuthProvider';
import { AoiMap } from './components/AoiMap';
import { AoiUpload } from './components/AoiUpload';
import { ClassifyInputsForm } from './components/ClassifyInputsForm';
import { ProcessingPanel } from './components/ProcessingPanel';
import { ResultStats } from './components/ResultStats';
import { DownloadPanel } from './components/DownloadPanel';
import { FieldDetail } from './components/FieldDetail';
import { HistoryMenu, HistoryPanel } from './components/HistoryPanel';
import type {
  AreaOfInterest,
  ClassificationHistoryItem,
  ClassificationInputs,
  ClassificationResult,
  JobProgress,
  JobStage,
} from './types';
import {
  DEFAULT_INPUTS,
  areasFromStored,
  formatHa,
  inputsFromStored,
  isJobStage,
} from './types';

const STEPS = [
  { id: 1, label: 'Area' },
  { id: 2, label: 'Inputs' },
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
                className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold border transition-colors ${
                  done
                    ? 'bg-emerald-500 border-emerald-500 text-white'
                    : active
                      ? 'bg-emerald-500/20 border-emerald-500 text-emerald-700'
                      : 'bg-white border-rule text-stone-400'
                }`}
              >
                {done ? '✓' : step.id}
              </span>
              <span
                className={`text-[11px] sm:text-xs font-medium truncate ${
                  active ? 'text-stone-900' : done ? 'text-stone-500' : 'text-stone-400'
                }`}
              >
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

export default function ClassificationPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  const [step, setStep] = useState(1);
  const [areas, setAreas] = useState<AreaOfInterest[]>([]);
  const [inputs, setInputs] = useState<ClassificationInputs>(DEFAULT_INPUTS);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [result, setResult] = useState<ClassificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedField, setSelectedField] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<ClassificationHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [openingId, setOpeningId] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openedFromUrl = useRef(false);
  const inputsRef = useRef(inputs);
  inputsRef.current = inputs;

  const activeJobId = result?.job_id || progress?.job_id || null;

  const setJobQuery = (id: string | null) => {
    if (typeof window === 'undefined') return;
    const url = id ? `/classification?job=${encodeURIComponent(id)}` : '/classification';
    window.history.replaceState(null, '', url);
  };

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [loading, user, router]);

  // One timer, cleared on unmount — a poll that outlives the page keeps the
  // job alive in the UI after navigation and double-fires on return.
  useEffect(() => {
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);

  const totalAreaHa = areas.reduce((s, a) => s + a.area_ha, 0);

  const loadHistory = useCallback(async (): Promise<ClassificationHistoryItem[]> => {
    try {
      const res = await fetch('/api/classification/history', { credentials: 'include' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Could not load history');
      const jobs = Array.isArray(data.jobs) ? (data.jobs as ClassificationHistoryItem[]) : [];
      setHistory(jobs);
      return jobs;
    } catch {
      setHistory([]);
      return [];
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const poll = useCallback(async (jobId: string) => {
    try {
      const res = await fetch(`/api/classification/status/${jobId}`, {
        credentials: 'include',
      });
      const data = (await res.json()) as JobProgress & { result?: ClassificationResult };
      if (!res.ok) throw new Error((data as { error?: string }).error || 'Status check failed');

      setProgress(data);

      if (data.stage === 'complete') {
        const r = await fetch(`/api/classification/result/${jobId}`, {
          credentials: 'include',
        });
        const payload = await r.json();
        if (!r.ok) throw new Error(payload.error || 'Could not load the result');
        const typed = (inputsRef.current.region_name || '').trim();
        setResult({
          ...(payload as ClassificationResult),
          aoi_name: typed || (payload as ClassificationResult).aoi_name,
        });
        setStep(4);
        void loadHistory();
        return;
      }
      if (data.stage === 'failed') {
        setError(data.error || 'Classification failed.');
        return;
      }
      pollRef.current = setTimeout(() => void poll(jobId), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lost contact with the job.');
    }
  }, [loadHistory]);

  const restoreContext = (item: ClassificationHistoryItem) => {
    const restoredAreas = areasFromStored(item.areas);
    if (restoredAreas.length) setAreas(restoredAreas);
    setInputs(inputsFromStored(item.inputs));
  };

  const openJob = useCallback(
    async (item: ClassificationHistoryItem) => {
      if (pollRef.current) clearTimeout(pollRef.current);
      setError(null);
      setSelectedField(null);
      setOpeningId(item.job_id);
      setJobQuery(item.job_id);
      restoreContext(item);

      try {
        if (item.stage === 'complete') {
          const r = await fetch(`/api/classification/result/${item.job_id}`, {
            credentials: 'include',
          });
          const payload = await r.json();
          if (!r.ok) throw new Error(payload.error || 'Could not load the result');
          const typed = (item.inputs?.region_name || '').trim();
          setResult({
            ...(payload as ClassificationResult),
            aoi_name: typed || (payload as ClassificationResult).aoi_name,
          });
          setProgress({
            job_id: item.job_id,
            stage: 'complete',
            percent: 100,
            checks: [],
          });
          setStep(4);
          return;
        }

        setResult(null);
        const stage: JobStage = isJobStage(item.stage) ? item.stage : 'queued';
        setProgress({
          job_id: item.job_id,
          stage,
          percent: null,
          checks: [],
          error: item.error,
        });
        if (stage === 'failed') {
          setError(item.error || 'Classification failed.');
          setStep(3);
          return;
        }
        setStep(3);
        pollRef.current = setTimeout(() => void poll(item.job_id), 400);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not open that run.');
      } finally {
        setOpeningId(null);
      }
    },
    [poll]
  );

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      const jobs = await loadHistory();
      if (cancelled || openedFromUrl.current) return;
      const id = new URLSearchParams(window.location.search).get('job');
      if (!id) return;
      openedFromUrl.current = true;
      const found = jobs.find((j) => j.job_id === id);
      if (found) {
        await openJob(found);
        return;
      }
      // Deep link to a job that is owned but not in the trimmed list: try result.
      try {
        const r = await fetch(`/api/classification/result/${id}`, { credentials: 'include' });
        const payload = await r.json();
        if (cancelled) return;
        if (r.ok) {
          setResult(payload as ClassificationResult);
          setProgress({ job_id: id, stage: 'complete', percent: 100, checks: [] });
          setStep(4);
        }
      } catch {
        /* listed jobs cover the common case */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, loadHistory, openJob]);

  const start = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const region = inputs.region_name.trim();
      const namedAreas = region ? areas.map((a) => ({ ...a, name: region })) : areas;
      const res = await fetch('/api/classification/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          areas: namedAreas,
          inputs: { ...inputs, region_name: region },
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Could not start the job');

      setResult(null);
      setSelectedField(null);
      setJobQuery(data.job_id);
      setStep(3);
      setProgress({
        job_id: data.job_id,
        stage: 'queued',
        percent: null,
        checks: [],
      });
      void loadHistory();
      pollRef.current = setTimeout(() => void poll(data.job_id), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start the job');
    } finally {
      setSubmitting(false);
    }
  };

  const reset = () => {
    if (pollRef.current) clearTimeout(pollRef.current);
    setStep(1);
    setAreas([]);
    setInputs(DEFAULT_INPUTS);
    setProgress(null);
    setResult(null);
    setError(null);
    setSelectedField(null);
    setJobQuery(null);
    void loadHistory();
  };

  if (loading || !user) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
        Loading…
      </div>
    );
  }

  const mapReadOnly = step >= 3;

  return (
    <div className="h-[100dvh] flex flex-col bg-paper text-stone-800 overflow-hidden">
      <header className="page-shell flex items-center justify-between py-2 border-b border-rule flex-shrink-0">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-stone-500 hover:text-emerald-700 text-sm">
            ← Home
          </Link>
          <div className="h-4 w-px bg-rule" />
          <div>
            <h1 className="text-sm font-bold text-stone-900 tracking-tight">Crop Classification</h1>
            <p className="text-[10px] text-stone-500 font-mono">Area-wide crop mapping</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/clusters" className="text-xs text-stone-500 hover:text-emerald-700">
            Clusters
          </Link>
          <Link href="/monitoring" className="text-xs text-stone-500 hover:text-emerald-700">
            Monitoring
          </Link>
          {step > 1 && (
            <>
              <HistoryMenu
                items={history}
                loading={historyLoading}
                activeJobId={activeJobId}
                openingId={openingId}
                onOpen={(item) => void openJob(item)}
              />
              <button
                onClick={reset}
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold px-3 py-1.5 hover:bg-emerald-700 shadow-sm transition-colors"
              >
                <span className="text-base leading-none" aria-hidden>
                  +
                </span>
                New classification
              </button>
            </>
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

        <div
          className={`grid gap-3 items-stretch flex-1 min-h-0 ${
            step === 4
              ? 'lg:grid-cols-[minmax(0,7fr)_minmax(260px,3fr)]'
              : 'lg:grid-cols-[1fr_400px]'
          }`}
        >
          <div className="flex flex-col min-w-0 min-h-0 lg:h-full">
            <AoiMap
              areas={areas}
              onAreasChange={setAreas}
              resultLayer={result?.fields ?? null}
              onFieldClick={setSelectedField}
              selectedFieldId={selectedField?.field_id != null ? String(selectedField.field_id) : null}
              readOnly={mapReadOnly}
              heightClass="h-[52dvh] lg:flex-1 lg:min-h-0"
              legend={step === 4 ? result?.stats ?? null : null}
              toolbar={step === 4 && result ? <DownloadPanel result={result} /> : undefined}
            />

            {step < 3 && (
              <p className="text-[11px] text-stone-500 mt-1.5 flex-shrink-0">
                Draw a polygon or rectangle with the tools at the top-right of the map, or upload a
                boundary file. Several areas are allowed — they are classified as one job.
              </p>
            )}
          </div>

          <aside className="rounded-2xl border border-rule bg-white/70 backdrop-blur-sm p-4 min-w-0 min-h-0 lg:overflow-y-auto">
            {step === 1 && (
              <div className="space-y-5">
                <div>
                  <h2 className="text-base font-bold text-stone-900 mb-1">Define the area</h2>
                  <p className="text-xs text-stone-500 leading-relaxed">
                    Draw on the map, or upload a village / block / AOI boundary.
                  </p>
                </div>
                <AoiUpload areas={areas} onAreasChange={setAreas} />
                <div className="pt-1">
                  <div className="flex items-baseline justify-between mb-3">
                    <span className="text-xs text-stone-500">
                      {areas.length} area{areas.length === 1 ? '' : 's'}
                    </span>
                    <span className="text-sm font-semibold text-stone-800">
                      {formatHa(totalAreaHa)}
                    </span>
                  </div>
                  <button
                    onClick={() => {
                      const suggested = areas[0]?.name || '';
                      if (
                        !inputs.region_name.trim() &&
                        suggested &&
                        !/^Drawn area\s+\d+$/i.test(suggested)
                      ) {
                        setInputs({ ...inputs, region_name: suggested });
                      }
                      setStep(2);
                    }}
                    disabled={areas.length === 0}
                    className="w-full rounded-xl bg-emerald-600 text-white text-sm font-semibold py-2.5 hover:bg-emerald-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Continue to inputs →
                  </button>
                </div>
                <div className="pt-4 border-t border-rule">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">
                    Past classifications
                  </h3>
                  <HistoryPanel
                    items={history}
                    loading={historyLoading}
                    activeJobId={activeJobId}
                    openingId={openingId}
                    onOpen={(item) => void openJob(item)}
                  />
                </div>
              </div>
            )}

            {step === 2 && (
              <div className="space-y-5">
                <div>
                  <h2 className="text-base font-bold text-stone-900 mb-1">Model inputs</h2>
                  <p className="text-xs text-stone-500 leading-relaxed">
                    What the classifier should look for, and how cautious it should be.
                  </p>
                </div>
                <ClassifyInputsForm
                  value={inputs}
                  onChange={setInputs}
                  totalAreaHa={totalAreaHa}
                />
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => setStep(1)}
                    className="rounded-xl border border-rule px-4 py-2.5 text-sm text-stone-600 hover:bg-stone-50 transition-colors"
                  >
                    ← Back
                  </button>
                  <button
                    onClick={() => void start()}
                    disabled={submitting || !inputs.region_name.trim()}
                    className="flex-1 rounded-xl bg-emerald-600 text-white text-sm font-semibold py-2.5 hover:bg-emerald-700 transition-colors disabled:opacity-60"
                  >
                    {submitting ? 'Starting…' : 'Run classification'}
                  </button>
                </div>
              </div>
            )}

            {step === 3 && progress && (
              <div className="space-y-5">
                <div>
                  <h2 className="text-base font-bold text-stone-900 mb-1">Processing</h2>
                  <p className="text-xs text-stone-500 font-mono">Job {progress.job_id}</p>
                </div>
                <ProcessingPanel progress={progress} />
                {progress.stage === 'failed' && (
                  <button
                    onClick={() => setStep(2)}
                    className="w-full rounded-xl border border-rule px-4 py-2.5 text-sm text-stone-600 hover:bg-stone-50 transition-colors"
                  >
                    ← Adjust inputs and retry
                  </button>
                )}
              </div>
            )}

            {step === 4 && result && (
              <div className="space-y-4">
                {selectedField && (
                  <FieldDetail field={selectedField} onClose={() => setSelectedField(null)} />
                )}
                <ResultStats result={result} />
              </div>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}
