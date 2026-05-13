'use client';

import { Suspense, useState, useEffect, FormEvent } from 'react';
import { useSearchParams } from 'next/navigation';
import type { AssessmentPayload, AssessmentJob } from '../types/assessment';
import { runAssessmentJob, pollJobStatus } from '../lib/assessmentClient';
import { LocationStrip } from './components/LocationStrip';
import { SummaryHero } from './components/SummaryHero';
import { CropCyclesSection } from './components/CropCyclesSection';
import { CroppingSection } from './components/CroppingSection';
import { PerformanceSection } from './components/PerformanceSection';
import { WeatherSection } from './components/WeatherSection';
import { AIEnrichmentSection } from './components/AIEnrichmentSection';
import Link from 'next/link';

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#0d1117] flex items-center justify-center text-gray-400 text-sm">
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
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<'IDLE' | 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED'>('IDLE');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [data, setData] = useState<AssessmentPayload | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  
  // Form State
  const [farmerId, setFarmerId] = useState('');
  const [pmKisanEnrolled, setPmKisanEnrolled] = useState(false);
  const [hasCropInsurance, setHasCropInsurance] = useState(false);

  // Tabs
  type TabId = 'overview' | 'cropPerf' | 'weather' | 'cycles' | 'ai';
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  const TABS: { id: TabId; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'cropPerf', label: 'Crop & Performance' },
    { id: 'weather', label: 'Weather' },
    { id: 'cycles', label: 'Cycles' },
    { id: 'ai', label: 'Explainability' },
  ];

  useEffect(() => {
    const qFarmerId = (searchParams.get('farmer_id') || '').trim();
    if (qFarmerId) {
      setFarmerId(qFarmerId);
    }
  }, [searchParams]);

  // Polling effect
  useEffect(() => {
    if (!jobId || status === 'SUCCESS' || status === 'FAILED') return;

    const interval = setInterval(async () => {
      try {
        const job = await pollJobStatus(jobId);
        
        setStatus(job.status);

        // Legacy jobs: Mongo status SUCCESS but pipeline payload says FAILED (older worker bug)
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
          setLoadingMsg('Pipeline is running. Computing satellite aggregations & ML models...');
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
        hasCropInsurance
      });
      setJobId(res.job_id);
      setStatus(res.status as 'QUEUED' | 'RUNNING');
    } catch (err) {
      setStatus('FAILED');
      setErrorMsg(err instanceof Error ? err.message : 'Failed to enqueue assessment');
    }
  };

  return (
    <div className="min-h-screen bg-[#0d1117] text-gray-200 selection:bg-emerald-500/30">
      <header className="bg-[#161b22] border-b border-[#30363d] sticky top-0 z-20">
        <div className="flex h-16 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-500 flex items-center justify-center font-bold text-[#0d1117]">A</div>
            <div>
              <h1 className="text-sm font-bold text-gray-100">Agri-Credit Dashboard</h1>
              <p className="text-[10px] text-gray-500 font-mono">v1.0.0-PROD</p>
            </div>
          </div>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/agristack" className="text-gray-400 hover:text-emerald-400 font-medium transition-colors">
              Data Acquisition (Agristack)
            </Link>
            <span className="text-[#30363d]">|</span>
            <span className="text-emerald-400 font-medium">Dashboard</span>
          </nav>
        </div>
      </header>

      <main className="p-6 max-w-7xl mx-auto w-full space-y-6">
        
        {/* Form Section */}
        {status === 'IDLE' && !data && (
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-8 max-w-2xl mx-auto mt-12 shadow-2xl">
            <h2 className="text-xl font-bold mb-6 text-gray-100">Run New Assessment</h2>
            
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-2">Farmer ID (from Agristack / DB)</label>
                <input
                  type="text"
                  value={farmerId}
                  onChange={(e) => setFarmerId(e.target.value)}
                  placeholder="e.g. 10001921019"
                  className="w-full bg-[#0d1117] border border-[#30363d] text-gray-200 rounded-lg p-3 placeholder-gray-600 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors font-mono text-sm"
                  required
                />
                <p className="text-xs text-gray-500 mt-2">
                  This ID must exist in the <code className="text-emerald-400">farm_info</code> collection.
                </p>
              </div>

              <div className="space-y-3 pt-2">
                <label className="flex items-center gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={pmKisanEnrolled}
                    onChange={(e) => setPmKisanEnrolled(e.target.checked)}
                    className="w-4 h-4 rounded border-[#30363d] bg-[#0d1117] text-emerald-500 focus:ring-emerald-500/20 focus:ring-offset-[#161b22]"
                  />
                  <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">PM-KISAN Enrolled</span>
                </label>
                <label className="flex items-center gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={hasCropInsurance}
                    onChange={(e) => setHasCropInsurance(e.target.checked)}
                    className="w-4 h-4 rounded border-[#30363d] bg-[#0d1117] text-emerald-500 focus:ring-emerald-500/20 focus:ring-offset-[#161b22]"
                  />
                  <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">Has Crop Insurance (PMFBY)</span>
                </label>
              </div>

              <div className="pt-4">
                <button
                  type="submit"
                  disabled={!farmerId.trim()}
                  className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:bg-[#30363d] disabled:text-gray-500 text-white font-bold py-3 px-4 rounded-lg transition-colors flex justify-center items-center gap-2"
                >
                  Run Pipeline
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Loading / Status State */}
        {(status === 'QUEUED' || status === 'RUNNING') && (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="relative w-16 h-16 mb-6">
              <div className="absolute inset-0 border-t-2 border-emerald-500 rounded-full animate-spin"></div>
              <div className="absolute inset-2 border-r-2 border-blue-500 rounded-full animate-spin" style={{ animationDirection: 'reverse', animationDuration: '1.5s' }}></div>
            </div>
            <h3 className="text-lg font-bold text-emerald-400 mb-2">Pipeline Working</h3>
            <p className="text-sm text-gray-400 font-mono">{loadingMsg}</p>
            <p className="text-[10px] text-gray-600 mt-4 uppercase tracking-widest">Job ID: {jobId}</p>
          </div>
        )}

        {/* Error State */}
        {status === 'FAILED' && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center max-w-2xl mx-auto mt-12">
            <div className="text-4xl mb-4">⚠️</div>
            <h3 className="text-lg font-bold text-red-500 mb-2">Assessment Failed</h3>
            <p className="text-sm text-red-400 mb-6">{errorMsg}</p>
            <button
              onClick={() => { setStatus('IDLE'); setJobId(null); }}
              className="bg-[#21262d] hover:bg-[#30363d] text-gray-300 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Try Again
            </button>
          </div>
        )}

        {/* Results View */}
        {data && status === 'SUCCESS' && (
          <div className="space-y-6 animate-slide-in">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-bold text-gray-100">Assessment Insights</h2>
              <button
                onClick={() => { setStatus('IDLE'); setData(null); setJobId(null); setFarmerId(''); }}
                className="bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-gray-300 px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
              >
                ← New Assessment
              </button>
            </div>

            {/* Top Level Nav */}
            <div className="flex bg-[#161b22] border border-[#30363d] rounded-lg p-1 gap-1 overflow-x-auto">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setActiveTab(t.id)}
                  className={`px-4 py-2 text-sm font-medium rounded-md whitespace-nowrap transition-colors ${
                    activeTab === t.id 
                      ? 'bg-[#30363d] text-gray-100 shadow-sm'
                      : 'text-gray-500 hover:text-gray-300 hover:bg-white/[0.02]'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {/* Content areas */}
            {activeTab === 'overview' && (
              <div className="space-y-6">
                <LocationStrip data={data} />
                <SummaryHero data={data} />
                <CropCyclesSection data={data} />
              </div>
            )}

            {activeTab === 'cropPerf' && (
              <div className="space-y-6">
                <CroppingSection data={data} />
                <PerformanceSection data={data} />
              </div>
            )}

            {activeTab === 'weather' && (
              <WeatherSection data={data} />
            )}

            {activeTab === 'cycles' && (
              <CropCyclesSection data={data} />
            )}

            {activeTab === 'ai' && (
              <AIEnrichmentSection data={data} />
            )}
          </div>
        )}

      </main>
    </div>
  );
}
