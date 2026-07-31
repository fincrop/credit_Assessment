'use client';

import { Suspense, useState, useEffect, FormEvent, useCallback } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import type { AssessmentPayload, TriState } from '../types/assessment';
import { runAssessmentJob, pollJobStatus } from '../lib/assessmentClient';
import { LocationStrip } from './components/LocationStrip';
import { SummaryHero } from './components/SummaryHero';
import { IndexInsightsCard } from './components/IndexInsightsCard';
import { SignalQualityStrip } from './components/SignalQualityStrip';
import { CropCyclesSection } from './components/CropCyclesSection';
import { CroppingSection } from './components/CroppingSection';
import { PerformanceSection } from './components/PerformanceSection';
import { WeatherSection } from './components/WeatherSection';
import { AIEnrichmentSection } from './components/AIEnrichmentSection';
import { AssessmentPrintReport } from './components/AssessmentPrintReport';
import { useRiskView } from '../lib/useRiskView';
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
  const [errorMsg, setErrorMsg] = useState('');

  const [farmerId, setFarmerId] = useState('');
  const [pmKisanEnrolled, setPmKisanEnrolled] = useState<TriState>(null);
  const [hasCropInsurance, setHasCropInsurance] = useState<TriState>(null);

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

  useEffect(() => {
    const qFarmerId = (searchParams.get('farmer_id') || '').trim();
    if (qFarmerId) setFarmerId(qFarmerId);

    const qJobId = (searchParams.get('job_id') || '').trim();
    if (qJobId && /^[a-f0-9]{24}$/i.test(qJobId) && !jobId) {
      setJobId(qJobId);
      setStatus('QUEUED');
      setLoadingMsg('Reconnecting to job…');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only hydrate once from URL
  }, []);

  useEffect(() => {
    if (!jobId || status === 'SUCCESS' || status === 'FAILED') return;

    const interval = setInterval(async () => {
      try {
        const job = await pollJobStatus(jobId);

        setStatus(job.status);

        const resultFailed =
          job.result &&
          typeof job.result === 'object' &&
          String((job.result as { status?: string }).status || '').toUpperCase() === 'FAILED';

        if (job.status === 'SUCCESS' && job.result && !resultFailed) {
          setData(job.result);
          clearInterval(interval);
        } else if (job.status === 'FAILED' || resultFailed) {
          const r = job.result as { error?: string } | undefined;
          setStatus('FAILED');
          setErrorMsg(job.error ?? r?.error ?? 'Pipeline failed unexpectedly');
          clearInterval(interval);
        } else if (job.status === 'RUNNING') {
          const prog = (job as { progress?: { current_stage?: string; pipeline_stages?: string[] } })
            .progress;
          const stage = prog?.current_stage || prog?.pipeline_stages?.slice(-1)[0];
          setLoadingMsg(
            stage
              ? `Pipeline running — stage: ${stage}`
              : 'Pipeline is running. Computing satellite aggregations & ML models...'
          );
        } else {
          setLoadingMsg('Job queued. Waiting for worker...');
        }
      } catch (err) {
        console.error('Error polling job status:', err);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [jobId, status]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!farmerId.trim()) return;

    setData(null);
    setErrorMsg('');
    setStatus('QUEUED');
    setLoadingMsg('Submitting job to queue...');

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
    setJobId(null);
    setFarmerId('');
    setPmKisanEnrolled(null);
    setHasCropInsurance(null);
    syncJobToUrl(null);
  };

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
            <Link
              href="/agristack"
              className="text-stone-500 hover:text-emerald-700 font-medium transition-colors"
            >
              Data Acquisition
            </Link>
            <span className="text-[#E4DFD4]">|</span>
            <Link
              href="/farmer"
              className="text-stone-500 hover:text-emerald-700 font-medium transition-colors"
            >
              Farmer Journey
            </Link>
            <span className="text-[#E4DFD4]">|</span>
            <span className="text-emerald-700 font-medium">Dashboard</span>
          </nav>
        </div>
      </header>

      <main className="p-6 max-w-7xl mx-auto w-full space-y-6 no-print">
        {status === 'IDLE' && !data && (
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
                <p className="text-xs text-stone-500 mt-2">
                  This ID must exist in the <code className="text-emerald-700">farm_info</code>{' '}
                  collection.
                </p>
              </div>

              <div className="grid sm:grid-cols-2 gap-4 pt-1">
                <TriStateSelect
                  label="PM-KISAN enrolled"
                  value={pmKisanEnrolled}
                  onChange={setPmKisanEnrolled}
                />
                <TriStateSelect
                  label="Crop insurance (PMFBY)"
                  value={hasCropInsurance}
                  onChange={setHasCropInsurance}
                />
              </div>
              <p className="text-xs text-stone-500">
                Unknown does not penalise the index (positive-only benefits).
              </p>

              <div className="pt-2">
                <button
                  type="submit"
                  disabled={!farmerId.trim()}
                  className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-200 disabled:text-stone-400 text-white font-bold py-3 px-4 rounded-lg transition-colors"
                >
                  Run Pipeline
                </button>
              </div>
            </form>
          </div>
        )}

        {(status === 'QUEUED' || status === 'RUNNING') && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="relative w-16 h-16 mb-6">
              <div className="absolute inset-0 border-t-2 border-emerald-500 rounded-full animate-spin"></div>
              <div
                className="absolute inset-2 border-r-2 border-sky-500 rounded-full animate-spin"
                style={{ animationDirection: 'reverse', animationDuration: '1.5s' }}
              ></div>
            </div>
            <h3 className="text-lg font-bold text-emerald-700 mb-2">Pipeline Working</h3>
            <p className="text-sm text-stone-500 font-mono">{loadingMsg}</p>
            <p className="text-[10px] text-stone-400 mt-4 uppercase tracking-widest">
              Job ID: {jobId}
            </p>
          </div>
        )}

        {status === 'FAILED' && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-center max-w-2xl mx-auto mt-12">
            <h3 className="text-lg font-bold text-red-700 mb-2">Assessment Failed</h3>
            <p className="text-sm text-red-600 mb-6">{errorMsg}</p>
            <button
              onClick={() => {
                setStatus('IDLE');
                setJobId(null);
                syncJobToUrl(null, farmerId || undefined);
              }}
              className="bg-white hover:bg-[#F5F2EB] border border-[#E4DFD4] text-stone-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Try Again
            </button>
          </div>
        )}

        {data && status === 'SUCCESS' && (
          <div className="space-y-6 animate-slide-in">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-xl font-bold text-stone-900">Assessment Insights</h2>
                {riskView.indexVersion && (
                  <p className="text-xs text-stone-500 font-mono mt-0.5">{riskView.indexVersion}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => window.print()}
                  className="bg-white hover:bg-[#F5F2EB] border border-[#E4DFD4] text-stone-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  Print / PDF
                </button>
                <button
                  type="button"
                  onClick={resetToIdle}
                  className="bg-white hover:bg-[#F5F2EB] border border-[#E4DFD4] text-stone-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  ← New Assessment
                </button>
              </div>
            </div>

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
                <LocationStrip data={data} />
                <SummaryHero data={data} />
                <SignalQualityStrip data={data} />
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
          </div>
        )}
      </main>

      {data && status === 'SUCCESS' && <AssessmentPrintReport data={data} />}
    </div>
  );
}
