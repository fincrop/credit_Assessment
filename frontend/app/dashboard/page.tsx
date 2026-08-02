'use client';

import { Suspense, useState, useEffect, FormEvent, useCallback, useMemo } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import type { AssessmentPayload, FarmAssessment, TriState } from '../types/assessment';
import { runAssessmentJob, pollJobStatusSafe } from '../lib/assessmentClient';
import { FarmerIdentityCard, type FarmInfoSummary } from './components/FarmerIdentityCard';
import { RiskScoreCard } from './components/RiskScoreCard';
import { StreamingFarmList } from './components/StreamingFarmList';
import { IndexInsightsCard } from './components/IndexInsightsCard';
import { CropCyclesSection } from './components/CropCyclesSection';
import { CroppingSection } from './components/CroppingSection';
import { PerformanceSection } from './components/PerformanceSection';
import { WeatherSection } from './components/WeatherSection';
import { AIEnrichmentSection } from './components/AIEnrichmentSection';
import { AssessmentPrintReport } from './components/AssessmentPrintReport';
import { useRiskView } from '../lib/useRiskView';
import {
  buildStreamRows,
  portfolioSummaryFromRows,
  countersFromAssessments,
} from '../lib/streamFarms';
import { assignPlotKeysClient } from '../lib/plotKey';
import Link from 'next/link';

function TriStateSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: TriState;
  onChange: (v: TriState) => void;
}) {
  return (
    <div>
      <label className="block text-sm font-semibold text-stone-700 mb-2">{label}</label>
      <select
        value={value === null ? 'unknown' : value ? 'yes' : 'no'}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === 'yes' ? true : v === 'no' ? false : null);
        }}
        className="w-full bg-[#F5F2EB] border border-[#E4DFD4] text-stone-800 rounded-lg p-3 text-sm focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
      >
        <option value="unknown">Unknown</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#F5F2EB] flex items-center justify-center text-stone-500 text-sm">
          Loading dashboard…
        </div>
      }
    >
      <DashboardPageContent />
    </Suspense>
  );
}

function DashboardPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<'IDLE' | 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED'>(
    'IDLE'
  );
  const [loadingMsg, setLoadingMsg] = useState('');
  const [data, setData] = useState<AssessmentPayload | null>(null);
  const [partialFarms, setPartialFarms] = useState<FarmAssessment[]>([]);
  const [errorMsg, setErrorMsg] = useState('');
  const [farmInfo, setFarmInfo] = useState<FarmInfoSummary | null>(null);
  const [seedFarms, setSeedFarms] = useState<Record<string, unknown>[]>([]);

  const [farmerId, setFarmerId] = useState('');
  const [pmKisanEnrolled, setPmKisanEnrolled] = useState<TriState>(null);
  const [hasCropInsurance, setHasCropInsurance] = useState<TriState>(null);

  const [historyLoading, setHistoryLoading] = useState(false);

  type TabId = 'overview' | 'cropPerf' | 'weather' | 'cycles' | 'ai';
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  const TABS: { id: TabId; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'cropPerf', label: 'Crop & Performance' },
    { id: 'weather', label: 'Weather' },
    { id: 'cycles', label: 'Cycles' },
    { id: 'ai', label: 'Explainability' },
  ];

  const riskView = useRiskView(data);
  const showShell = status === 'QUEUED' || status === 'RUNNING' || status === 'SUCCESS' || (status === 'FAILED' && !!farmInfo);

  const syncJobToUrl = useCallback(
    (id: string | null, farmer?: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (id) params.set('job_id', id);
      else params.delete('job_id');
      if (farmer) params.set('farmer_id', farmer);
      const qs = params.toString();
      router.replace(qs ? `/dashboard?${qs}` : '/dashboard', { scroll: false });
    },
    [router, searchParams]
  );

  const loadFarmInfo = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/farm-info/${encodeURIComponent(id)}`, {
        credentials: 'include',
      });
      const json = await res.json();
      if (!res.ok) {
        setFarmInfo(null);
        setSeedFarms([]);
        return;
      }
      const fi = json.farm_info;
      setFarmInfo({
        farmer_id: fi.farmer_id,
        name: fi.name,
        mobile: fi.mobile,
        state: fi.state,
        district: fi.district,
        village: fi.village,
        farmer_benefits: fi.farmer_benefits,
      });
      const farms = assignPlotKeysClient(
        Array.isArray(fi.farms) ? fi.farms : []
      );
      setSeedFarms(farms);
    } catch {
      setFarmInfo(null);
      setSeedFarms([]);
    }
  }, []);

  const loadLatestAssessment = useCallback(async (id: string) => {
    setHistoryLoading(true);
    setLoadingMsg('Loading last assessment…');
    try {
      const res = await fetch(
        `/api/assessments/latest?farmer_id=${encodeURIComponent(id)}`,
        { credentials: 'include', cache: 'no-store' }
      );
      const json = await res.json();
      if (!res.ok || !json.assessment) return false;
      const payload = json.assessment as AssessmentPayload;
      setData(payload);
      if (Array.isArray(payload.farm_assessments)) {
        setPartialFarms(payload.farm_assessments);
      }
      setStatus('SUCCESS');
      setLoadingMsg('');
      return true;
    } catch {
      return false;
    } finally {
      setHistoryLoading(false);
      setLoadingMsg('');
    }
  }, []);

  useEffect(() => {
    const qFarmerId = (searchParams.get('farmer_id') || '').trim();
    const qJobId = (searchParams.get('job_id') || '').trim();

    if (qFarmerId) {
      setFarmerId(qFarmerId);
      void loadFarmInfo(qFarmerId);
    }

    if (qJobId && /^[a-f0-9]{24}$/i.test(qJobId) && !jobId) {
      setJobId(qJobId);
      setStatus('QUEUED');
      setLoadingMsg('Reconnecting to job…');
      return;
    }

    // Deep-link with farmer only → open stored results when available
    if (qFarmerId && !qJobId) {
      void loadLatestAssessment(qFarmerId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRerun = () => {
    setData(null);
    setPartialFarms([]);
    setJobId(null);
    setErrorMsg('');
    setStatus('IDLE');
    setLoadingMsg('');
    const params = new URLSearchParams();
    if (farmerId.trim()) params.set('farmer_id', farmerId.trim());
    router.replace(params.toString() ? `/dashboard?${params}` : '/dashboard', { scroll: false });
  };
  useEffect(() => {
    if (!jobId || status === 'SUCCESS' || status === 'FAILED') return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let failStreak = 0;

    const schedule = (ms: number) => {
      if (cancelled) return;
      timer = setTimeout(tick, ms);
    };

    const tick = async () => {
      if (cancelled || inFlight) {
        schedule(failStreak > 0 ? Math.min(10000, 2000 * (failStreak + 1)) : 2500);
        return;
      }
      inFlight = true;
      try {
        const polled = await pollJobStatusSafe(jobId);
        if (!polled.ok) {
          failStreak += 1;
          if (polled.transient || /DNS|SRV|ETIMEOUT|Mongo/i.test(polled.message)) {
            setLoadingMsg(
              'Database briefly unreachable (DNS). Retrying… assessment keeps running on the server.'
            );
          }
          schedule(Math.min(12000, 3000 * failStreak));
          return;
        }

        const job = polled.job;
        failStreak = 0;

        setStatus(job.status);

        if (job.farmer_id && !farmInfo) {
          void loadFarmInfo(String(job.farmer_id));
        }

        const prog = job.progress;
        if (prog?.partial_result?.farm_assessments) {
          setPartialFarms(prog.partial_result.farm_assessments);
        }

        const resultFailed =
          job.result &&
          typeof job.result === 'object' &&
          String((job.result as { status?: string }).status || '').toUpperCase() === 'FAILED';

        if (job.status === 'SUCCESS' && job.result && !resultFailed) {
          setData(job.result);
          if (job.result.farm_assessments) {
            setPartialFarms(job.result.farm_assessments);
          }
          return; // stop polling
        } else if (job.status === 'FAILED' || resultFailed) {
          const r = job.result as AssessmentPayload | undefined;
          setStatus('FAILED');
          setErrorMsg(
            job.error ?? (r as { error?: string })?.error ?? 'Pipeline failed unexpectedly'
          );
          if (r) {
            setData(r);
            if (r.farm_assessments) setPartialFarms(r.farm_assessments);
          }
          return;
        } else if (job.status === 'RUNNING') {
          const stage = prog?.current_stage || prog?.pipeline_stages?.slice(-1)[0];
          const done = prog?.n_plots_done;
          const total = prog?.n_plots_total;
          setLoadingMsg(
            stage
              ? `Analyzing — ${stage}${done != null && total != null ? ` (${done}/${total})` : ''}`
              : 'Pipeline is running…'
          );
        } else {
          setLoadingMsg('Job queued. Waiting for worker…');
        }
        schedule(2500);
      } finally {
        inFlight = false;
      }
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, status, farmInfo, loadFarmInfo]);

  const streamRows = useMemo(
    () =>
      buildStreamRows({
        seedFarms,
        partialAssessments: partialFarms,
        finalAssessments: data?.farm_assessments,
        jobStatus: status,
      }),
    [seedFarms, partialFarms, data, status]
  );

  const doneCounters = useMemo(() => {
    if (partialFarms.length) return countersFromAssessments(partialFarms);
    if (data?.farm_assessments) return countersFromAssessments(data.farm_assessments);
    const done = streamRows.filter(
      (r) => r.status !== 'pending' && r.status !== 'analyzing'
    ).length;
    const scored = streamRows.filter((r) => r.status === 'scored').length;
    return {
      n_plots_done: done,
      n_plots_total: streamRows.length,
      n_plots_scored: scored,
      n_plots_skipped: 0,
      n_plots_failed: 0,
    };
  }, [partialFarms, data, streamRows]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!farmerId.trim()) return;

    setData(null);
    setPartialFarms([]);
    setErrorMsg('');
    setStatus('QUEUED');
    setLoadingMsg('Submitting job…');
    await loadFarmInfo(farmerId.trim());

    try {
      const res = await runAssessmentJob({
        farmerId,
        pmKisanEnrolled,
        hasCropInsurance,
      });
      setJobId(res.job_id);
      setStatus(res.status as 'QUEUED' | 'RUNNING');
      syncJobToUrl(res.job_id, farmerId.trim());
    } catch (err) {
      setStatus('FAILED');
      setErrorMsg(err instanceof Error ? err.message : 'Failed to enqueue assessment');
    }
  };

  const resetToIdle = () => {
    setStatus('IDLE');
    setData(null);
    setPartialFarms([]);
    setJobId(null);
    setFarmInfo(null);
    setSeedFarms([]);
    setFarmerId('');
    setPmKisanEnrolled(null);
    setHasCropInsurance(null);
    syncJobToUrl(null);
  };

  const assessedLabel =
    data?.assessment_date
      ? new Date(data.assessment_date).toLocaleString('en-IN', {
          dateStyle: 'medium',
          timeStyle: 'short',
        })
      : undefined;

  const plotsLabel =
    doneCounters.n_plots_total != null
      ? `${(data?.n_plots_scored ?? doneCounters.n_plots_scored ?? 0)}/${doneCounters.n_plots_total} scored`
      : undefined;

  return (
    <div className="min-h-screen bg-[#F5F2EB] text-stone-800 selection:bg-emerald-500/30">
      <header className="bg-white border-b border-[#E4DFD4] sticky top-0 z-20 no-print">
        <div className="flex h-16 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-500 flex items-center justify-center font-bold text-[#F5F2EB]">
              A
            </div>
            <div>
              <h1 className="text-sm font-bold text-stone-900">Agri-Credit Dashboard</h1>
              <p className="text-[10px] text-stone-500 font-mono">Risk Index v5</p>
            </div>
          </div>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/" className="text-stone-500 hover:text-emerald-700 font-medium transition-colors">
              Home
            </Link>
            <span className="text-[#E4DFD4]">|</span>
            <Link href="/agristack" className="text-stone-500 hover:text-emerald-700 font-medium transition-colors">
              Data Acquisition
            </Link>
            <span className="text-[#E4DFD4]">|</span>
            <Link href="/farmer" className="text-stone-500 hover:text-emerald-700 font-medium transition-colors">
              Farmer Journey
            </Link>
            <span className="text-[#E4DFD4]">|</span>
            <span className="text-emerald-700 font-medium">Dashboard</span>
          </nav>
        </div>
      </header>

      <main className="p-6 max-w-7xl mx-auto w-full space-y-6 no-print">
        {historyLoading && status === 'IDLE' && !data && (
          <p className="text-center text-sm text-stone-500 mt-16">Loading last assessment…</p>
        )}

        {status === 'IDLE' && !data && !historyLoading && (
          <div className="bg-white border border-[#E4DFD4] rounded-xl p-8 max-w-2xl mx-auto mt-12 shadow-sm">
            <h2 className="text-xl font-bold mb-6 text-stone-900">Run New Assessment</h2>
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="block text-sm font-semibold text-stone-700 mb-2">
                  Farmer ID (from Agristack / DB)
                </label>
                <input
                  type="text"
                  value={farmerId}
                  onChange={(e) => setFarmerId(e.target.value)}
                  placeholder="e.g. 10001921019"
                  className="w-full bg-[#F5F2EB] border border-[#E4DFD4] text-stone-800 rounded-lg p-3 placeholder-stone-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors font-mono text-sm"
                  required
                />
              </div>
              <div className="grid sm:grid-cols-2 gap-4 pt-1">
                <TriStateSelect label="PM-KISAN enrolled" value={pmKisanEnrolled} onChange={setPmKisanEnrolled} />
                <TriStateSelect label="Crop insurance (PMFBY)" value={hasCropInsurance} onChange={setHasCropInsurance} />
              </div>
              <button
                type="submit"
                disabled={!farmerId.trim()}
                className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-200 disabled:text-stone-400 text-white font-bold py-3 px-4 rounded-lg transition-colors"
              >
                Run Pipeline
              </button>
            </form>
          </div>
        )}

        {showShell && (
          <div className="space-y-6 animate-slide-in">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-xl font-bold text-stone-900">Assessment Insights</h2>
                <p className="text-xs text-stone-500 font-mono mt-0.5">
                  {status === 'SUCCESS'
                    ? riskView.indexVersion || 'complete'
                    : status === 'FAILED'
                      ? 'failed'
                      : loadingMsg || status}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {status === 'SUCCESS' && data && (
                  <button
                    type="button"
                    onClick={() => window.print()}
                    className="bg-white hover:bg-[#F5F2EB] border border-[#E4DFD4] text-stone-700 px-4 py-2 rounded-lg text-sm font-medium"
                  >
                    Print / PDF
                  </button>
                )}
                {status === 'SUCCESS' && farmerId.trim() && (
                  <button
                    type="button"
                    onClick={startRerun}
                    className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium"
                  >
                    Re-run Assessment
                  </button>
                )}
                <button
                  type="button"
                  onClick={resetToIdle}
                  className="bg-white hover:bg-[#F5F2EB] border border-[#E4DFD4] text-stone-700 px-4 py-2 rounded-lg text-sm font-medium"
                >
                  ← New Assessment
                </button>
              </div>
            </div>

            {status === 'FAILED' && (
              <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
                {errorMsg || 'Assessment failed'}
              </div>
            )}

            <div className="grid lg:grid-cols-[35%_1fr] gap-5 items-stretch">
              <FarmerIdentityCard
                farmInfo={farmInfo}
                farmerId={farmerId || farmInfo?.farmer_id || data?.farmer_id || '—'}
                plotsLabel={plotsLabel}
                assessedLabel={assessedLabel}
                portfolioSummary={portfolioSummaryFromRows(streamRows)}
                pmKisan={
                  pmKisanEnrolled ??
                  data?.farmer_benefits?.pm_kisan_enrolled ??
                  riskView.benefits.pm_kisan
                }
                cropInsurance={
                  hasCropInsurance ??
                  data?.farmer_benefits?.has_crop_insurance ??
                  riskView.benefits.has_crop_insurance
                }
              />
              <RiskScoreCard
                data={status === 'SUCCESS' && data ? data : null}
                placeholder={status !== 'SUCCESS' || !data}
                statusMessage={
                  status === 'FAILED'
                    ? errorMsg || 'No farmer score — see plot outcomes below.'
                    : loadingMsg || 'Analyzing plots…'
                }
              />
            </div>

            <StreamingFarmList
              rows={streamRows}
              jobId={jobId}
              farmerId={farmerId || String(data?.farmer_id || '')}
              doneCount={doneCounters.n_plots_done ?? 0}
              totalCount={doneCounters.n_plots_total ?? streamRows.length}
            />

            {status === 'SUCCESS' && data && (
              <>
                <div className="flex bg-white border border-[#E4DFD4] rounded-lg p-1 gap-1 overflow-x-auto">
                  {TABS.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setActiveTab(t.id)}
                      className={`px-4 py-2 text-sm font-medium rounded-md whitespace-nowrap transition-colors ${
                        activeTab === t.id
                          ? 'bg-emerald-600 text-white shadow-sm'
                          : 'text-stone-500 hover:text-stone-800 hover:bg-[#F5F2EB]'
                      }`}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>

                {activeTab === 'overview' && (
                  <div className="space-y-6">
                    <IndexInsightsCard view={riskView} />
                  </div>
                )}
                {activeTab === 'cropPerf' && (
                  <div className="space-y-6">
                    <CroppingSection data={data} />
                    <PerformanceSection data={data} />
                  </div>
                )}
                {activeTab === 'weather' && <WeatherSection data={data} />}
                {activeTab === 'cycles' && <CropCyclesSection data={data} />}
                {activeTab === 'ai' && <AIEnrichmentSection data={data} />}
              </>
            )}
          </div>
        )}
      </main>

      {data && status === 'SUCCESS' && <AssessmentPrintReport data={data} />}
    </div>
  );
}
