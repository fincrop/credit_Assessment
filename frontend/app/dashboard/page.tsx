'use client';

import { Suspense, useState, useEffect, FormEvent, useCallback, useMemo, useRef } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import type { AssessmentPayload, FarmAssessment, TriState } from '../types/assessment';
import { runAssessmentJob, pollJobStatusSafe } from '../lib/assessmentClient';
import { FarmerIdentityCard, type FarmInfoSummary } from './components/FarmerIdentityCard';
import { RiskScoreCard } from './components/RiskScoreCard';
import { StreamingFarmList } from './components/StreamingFarmList';
import { FarmSelectPanel } from './components/FarmSelectPanel';
import { PlotBoundaryMap } from './components/PlotBoundaryMap';
import { RefusalPanel } from './components/RefusalPanel';
import { ScoreWaterfall } from './components/ScoreWaterfall';
import { LandCoverPanel } from './components/LandCoverPanel';
import { ProvenanceFooter } from './components/ProvenanceFooter';
import { ConfidenceStrip } from './components/ConfidenceBadge';
import { useRiskView } from '../lib/useRiskView';
import { terminalStateOf } from '../lib/terminalState';
import {
  buildStreamRows,
  portfolioSummaryFromRows,
  countersFromAssessments,
} from '../lib/streamFarms';
import { assignPlotKeysClient } from '../lib/plotKey';
import Link from 'next/link';
import {
  hasAgriStackSession,
  readSessionCreds,
} from '../lib/agristackSession';

function plotKeyOf(f: Record<string, unknown>, i: number) {
  return String(f.plot_key || f.farm_id || `plot_${i}`);
}

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
        className="w-full bg-paper border border-rule text-stone-800 rounded-lg p-3 text-sm focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
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
        <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
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
  const [status, setStatus] = useState<
    'IDLE' | 'PREPARING' | 'SELECT' | 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'FAILED'
  >('IDLE');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [data, setData] = useState<AssessmentPayload | null>(null);
  const [partialFarms, setPartialFarms] = useState<FarmAssessment[]>([]);
  const [errorMsg, setErrorMsg] = useState('');
  const [farmInfo, setFarmInfo] = useState<FarmInfoSummary | null>(null);
  const [seedFarms, setSeedFarms] = useState<Record<string, unknown>[]>([]);
  const [selectedPlotKeys, setSelectedPlotKeys] = useState<Set<string>>(new Set());
  const [assessBusy, setAssessBusy] = useState(false);
  const [focusedPlotKey, setFocusedPlotKey] = useState<string | null>(null);

  const [farmerId, setFarmerId] = useState('');
  const [pmKisanEnrolled, setPmKisanEnrolled] = useState<TriState>(null);
  const [hasCropInsurance, setHasCropInsurance] = useState<TriState>(null);

  const [historyLoading, setHistoryLoading] = useState(false);
  const lastGoodDataRef = useRef<AssessmentPayload | null>(null);
  const [agriSessionOk, setAgriSessionOk] = useState(false);

  useEffect(() => {
    setAgriSessionOk(hasAgriStackSession());
  }, [status]);

  const riskView = useRiskView(data);
  const refusal = useMemo(() => terminalStateOf(data), [data]);
  const showShell =
    status === 'QUEUED' ||
    status === 'RUNNING' ||
    status === 'SUCCESS' ||
    (status === 'FAILED' && !!farmInfo);

  const enterSelectWithFarms = useCallback(
    (
      farmsIn: Record<string, unknown>[],
      info?: FarmInfoSummary | null,
      id?: string
    ) => {
      const farms = assignPlotKeysClient(farmsIn);
      setSeedFarms(farms);
      if (info) setFarmInfo(info);
      if (id) setFarmerId(id);
      const keys = new Set(
        farms
          .map((f, i) => plotKeyOf(f, i))
          .filter((k, i) => farms[i]?.included_in_assessment !== false)
      );
      // Default: all selected if none flagged
      if (keys.size === 0) {
        farms.forEach((f, i) => keys.add(plotKeyOf(f, i)));
      }
      setSelectedPlotKeys(keys);
      setStatus('SELECT');
      setErrorMsg('');
      setLoadingMsg('');
    },
    []
  );

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
        return null;
      }
      const fi = json.farm_info;
      const summary: FarmInfoSummary = {
        farmer_id: fi.farmer_id,
        name: fi.name,
        mobile: fi.mobile,
        state: fi.state,
        district: fi.district,
        village: fi.village,
        farmer_benefits: fi.farmer_benefits,
      };
      setFarmInfo(summary);
      const farms = assignPlotKeysClient(
        Array.isArray(fi.farms) ? fi.farms : []
      );
      setSeedFarms(farms);
      return { summary, farms };
    } catch {
      setFarmInfo(null);
      setSeedFarms([]);
      return null;
    }
  }, []);

  useEffect(() => {
    const qFarmerId = (searchParams.get('farmer_id') || '').trim();
    const qJobId = (searchParams.get('job_id') || '').trim();

    if (qFarmerId) {
      setFarmerId(qFarmerId);
    }

    if (qJobId && /^[a-f0-9]{24}$/i.test(qJobId) && !jobId) {
      setJobId(qJobId);
      setStatus('QUEUED');
      setLoadingMsg('Reconnecting to job…');
      if (qFarmerId) void loadFarmInfo(qFarmerId);
      return;
    }

    // Deep-link with farmer only → open farm SELECT when farm_info exists
    if (qFarmerId && !qJobId) {
      void (async () => {
        setHistoryLoading(true);
        const loaded = await loadFarmInfo(qFarmerId);
        setHistoryLoading(false);
        if (loaded && loaded.farms.length > 0) {
          enterSelectWithFarms(loaded.farms, loaded.summary, qFarmerId);
          return;
        }
        // No farms yet — stay IDLE with ID prefilled (user can Prepare)
        setStatus('IDLE');
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRerun = () => {
    setData(null);
    setPartialFarms([]);
    setJobId(null);
    setErrorMsg('');
    setLoadingMsg('');
    if (seedFarms.length > 0) {
      enterSelectWithFarms(seedFarms, farmInfo, farmerId.trim() || undefined);
    } else {
      setStatus('IDLE');
    }
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
        schedule(failStreak > 0 ? Math.min(10000, 2000 * (failStreak + 1)) : 1500);
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
          lastGoodDataRef.current = job.result;
          if (job.result.farm_assessments) {
            setPartialFarms(job.result.farm_assessments);
          }
          setErrorMsg('');
          return; // stop polling
        } else if (job.status === 'FAILED' || resultFailed) {
          const r = job.result as AssessmentPayload | undefined;
          const failMsg =
            job.error ?? (r as { error?: string })?.error ?? 'Pipeline failed unexpectedly';
          if (lastGoodDataRef.current) {
            setData(lastGoodDataRef.current);
            if (lastGoodDataRef.current.farm_assessments) {
              setPartialFarms(lastGoodDataRef.current.farm_assessments);
            }
            setStatus('SUCCESS');
            setErrorMsg(`Re-run failed: ${failMsg}. Showing previous assessment.`);
          } else {
            setStatus('FAILED');
            setErrorMsg(failMsg);
            if (r) {
              setData(r);
              if (r.farm_assessments) setPartialFarms(r.farm_assessments);
            }
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
        schedule(1500);
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

  const portfolioMapPlots = useMemo(() => {
    return seedFarms.map((f, i) => {
      const key = plotKeyOf(f, i);
      const c = f.centroid as { lat?: number; lng?: number } | undefined;
      const geom = (f.geometry || f.boundary) as
        | { type?: string; coordinates?: unknown }
        | null
        | undefined;
      return {
        plot_key: key,
        label: String(f.farm_name || f.farm_id || key),
        geometry: geom || null,
        centroid:
          c?.lat != null && c?.lng != null
            ? { lat: Number(c.lat), lng: Number(c.lng) }
            : null,
      };
    });
  }, [seedFarms]);

  // Keep map focus on a real plot when the farm list changes
  useEffect(() => {
    if (!portfolioMapPlots.length) {
      setFocusedPlotKey(null);
      return;
    }
    setFocusedPlotKey((prev) => {
      if (prev && portfolioMapPlots.some((p) => p.plot_key === prev)) return prev;
      return portfolioMapPlots[0].plot_key || null;
    });
  }, [portfolioMapPlots]);

  const handlePrepare = async (e: FormEvent) => {
    e.preventDefault();
    if (!farmerId.trim()) return;

    if (!hasAgriStackSession()) {
      const next = `/dashboard${farmerId.trim() ? `?farmer_id=${encodeURIComponent(farmerId.trim())}` : ''}`;
      router.push(`/agristack/connect?next=${encodeURIComponent(next)}`);
      return;
    }
    const agriCreds = readSessionCreds();
    if (!agriCreds) {
      router.push('/agristack/connect?next=/dashboard');
      return;
    }

    setData(null);
    setPartialFarms([]);
    setErrorMsg('');
    setJobId(null);
    setStatus('PREPARING');
    setLoadingMsg('Preparing farms (AgriStack token → seek → webhook)…');
    syncJobToUrl(null, farmerId.trim());

    try {
      const res = await fetch('/api/agristack/prepare-farmer', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          farmer_id: farmerId.trim(),
          username: agriCreds.username,
          password: agriCreds.password,
          client_id: agriCreds.client_id,
        }),
      });
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json?.error || `Prepare failed (HTTP ${res.status})`);
      }
      const farms = Array.isArray(json.farms) ? json.farms : [];
      if (!farms.length) {
        throw new Error('No farm plots returned after prepare');
      }
      const summary: FarmInfoSummary | null = json.farm_info
        ? {
            farmer_id: json.farm_info.farmer_id,
            name: json.farm_info.name,
            mobile: json.farm_info.mobile,
            state: json.farm_info.state,
            district: json.farm_info.district,
            village: json.farm_info.village,
            farmer_benefits: json.farm_info.farmer_benefits,
          }
        : null;
      enterSelectWithFarms(farms, summary, farmerId.trim());
      setLoadingMsg(
        json.skipped_seek
          ? 'Loaded farms from database'
          : 'Farms ready — select plots to assess'
      );
    } catch (err) {
      setStatus('IDLE');
      setErrorMsg(err instanceof Error ? err.message : 'Failed to prepare farms');
      setLoadingMsg('');
    }
  };

  const persistInclusionAndEnqueue = async (keys: Set<string>) => {
    const id = farmerId.trim();
    if (!id) return;
    setAssessBusy(true);
    setErrorMsg('');
    try {
      const patchRes = await fetch(`/api/farm-info/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ farm_ids_included: Array.from(keys) }),
      });
      const patchJson = await patchRes.json();
      if (!patchRes.ok) {
        throw new Error(patchJson?.error || 'Failed to save plot selection');
      }
      if (Array.isArray(patchJson.farms)) {
        setSeedFarms(assignPlotKeysClient(patchJson.farms));
      }

      setPartialFarms([]);
      setStatus('QUEUED');
      setLoadingMsg('Submitting job…');
      // Keep last successful payload visible until the new job finishes.

      const res = await runAssessmentJob({
        farmerId: id,
        pmKisanEnrolled,
        hasCropInsurance,
      });
      setJobId(res.job_id);
      // Treat as RUNNING immediately — API claims the job before pipeline warm-up.
      setStatus('RUNNING');
      setLoadingMsg('Starting analysis…');
      syncJobToUrl(res.job_id, id);
    } catch (err) {
      setStatus('SELECT');
      setErrorMsg(err instanceof Error ? err.message : 'Failed to enqueue assessment');
    } finally {
      setAssessBusy(false);
    }
  };

  const handleAssessSelected = () => {
    void persistInclusionAndEnqueue(selectedPlotKeys);
  };

  const handleAssessAll = () => {
    const all = new Set(seedFarms.map((f, i) => plotKeyOf(f, i)));
    setSelectedPlotKeys(all);
    void persistInclusionAndEnqueue(all);
  };

  const resetToIdle = () => {
    setStatus('IDLE');
    setData(null);
    lastGoodDataRef.current = null;
    setPartialFarms([]);
    setJobId(null);
    setFarmInfo(null);
    setSeedFarms([]);
    setSelectedPlotKeys(new Set());
    setFarmerId('');
    setPmKisanEnrolled(null);
    setHasCropInsurance(null);
    setErrorMsg('');
    setLoadingMsg('');
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
    <div className="min-h-screen bg-paper text-stone-800 selection:bg-emerald-500/30">
      <header className="bg-white border-b border-rule sticky top-0 z-20 no-print">
        <div className="flex h-16 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-emerald-500 flex items-center justify-center font-bold text-paper">
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
            <span className="text-rule">|</span>
            <Link href="/agristack" className="text-stone-500 hover:text-emerald-700 font-medium transition-colors">
              API sandbox
            </Link>
            <span className="text-rule">|</span>
            <Link href="/farmer" className="text-stone-500 hover:text-emerald-700 font-medium transition-colors">
              Farmer Journey
            </Link>
            <span className="text-rule">|</span>
            <span className="text-emerald-700 font-medium">Dashboard</span>
          </nav>
        </div>
      </header>

      <main className="p-6 max-w-7xl mx-auto w-full space-y-6 no-print">
        {historyLoading && status === 'IDLE' && !data && (
          <p className="text-center text-sm text-stone-500 mt-16">Loading farms…</p>
        )}

        {errorMsg && (status === 'IDLE' || status === 'SELECT' || status === 'PREPARING') && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700 max-w-2xl mx-auto">
            {errorMsg}
          </div>
        )}

        {status === 'IDLE' && !data && !historyLoading && (
          <div className="bg-white border border-rule rounded-xl p-8 max-w-2xl mx-auto mt-12 shadow-sm">
            <h2 className="text-xl font-bold mb-2 text-stone-900">Run New Assessment</h2>
            <p className="text-sm text-stone-500 mb-6">
              Enter a Farmer ID. We fetch AgriStack land records in the background, then you
              choose which plots to score.
            </p>
            <div
              className={`mb-5 rounded-lg px-3 py-2 text-xs ${
                agriSessionOk
                  ? 'bg-emerald-50 border border-emerald-200 text-emerald-800'
                  : 'bg-amber-50 border border-amber-200 text-amber-900'
              }`}
            >
              {agriSessionOk ? (
                <>AgriStack session active for this browser tab.</>
              ) : (
                <>
                  AgriStack credentials required.{' '}
                  <Link
                    href={`/agristack/connect?next=${encodeURIComponent(
                      `/dashboard${farmerId.trim() ? `?farmer_id=${encodeURIComponent(farmerId.trim())}` : ''}`
                    )}`}
                    className="font-semibold underline underline-offset-2"
                  >
                    Sign in to AgriStack
                  </Link>
                </>
              )}
            </div>
            <form onSubmit={handlePrepare} className="space-y-5">
              <div>
                <label className="block text-sm font-semibold text-stone-700 mb-2">
                  Farmer ID (from Agristack / DB)
                </label>
                <input
                  type="text"
                  value={farmerId}
                  onChange={(e) => setFarmerId(e.target.value)}
                  placeholder="e.g. 10001921019"
                  className="w-full bg-paper border border-rule text-stone-800 rounded-lg p-3 placeholder-stone-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-colors font-mono text-sm"
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
                className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-200 disabled:text-ink-muted text-white font-bold py-3 px-4 rounded-lg transition-colors"
              >
                Prepare farms
              </button>
            </form>
          </div>
        )}

        {status === 'PREPARING' && (
          <div className="bg-white border border-rule rounded-xl p-8 max-w-2xl mx-auto mt-12 shadow-sm text-center space-y-3">
            <div className="mx-auto w-10 h-10 border-2 border-emerald-600 border-t-transparent rounded-full animate-spin" />
            <h2 className="text-lg font-bold text-stone-900">Preparing farms</h2>
            <p className="text-sm text-stone-500">{loadingMsg || 'Working…'}</p>
            <p className="text-xs text-ink-muted font-mono">{farmerId}</p>
          </div>
        )}

        {status === 'SELECT' && (
          <FarmSelectPanel
            farms={seedFarms}
            selectedKeys={selectedPlotKeys}
            busy={assessBusy}
            onToggle={(key) => {
              setSelectedPlotKeys((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              });
            }}
            onSelectAll={() =>
              setSelectedPlotKeys(new Set(seedFarms.map((f, i) => plotKeyOf(f, i))))
            }
            onClearAll={() => setSelectedPlotKeys(new Set())}
            onAssessSelected={handleAssessSelected}
            onAssessAll={handleAssessAll}
            onBack={() => {
              setStatus('IDLE');
              setErrorMsg('');
            }}
          />
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
                {status === 'SUCCESS' && farmerId.trim() && (
                  <>
                    <Link
                      href={`/report/${encodeURIComponent(farmerId.trim())}`}
                      className="bg-white hover:bg-paper border border-rule text-stone-700 px-4 py-2 rounded-lg text-sm font-medium"
                    >
                      Open report
                    </Link>
                    <button
                      type="button"
                      onClick={startRerun}
                      className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg text-sm font-medium"
                    >
                      Re-run Assessment
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={resetToIdle}
                  className="bg-white hover:bg-paper border border-rule text-stone-700 px-4 py-2 rounded-lg text-sm font-medium"
                >
                  ← New Assessment
                </button>
              </div>
            </div>

            {errorMsg && (
              <div
                className={`rounded-xl px-4 py-3 text-sm ${
                  status === 'FAILED'
                    ? 'bg-red-50 border border-red-200 text-red-700'
                    : 'bg-amber-50 border border-amber-200 text-amber-900'
                }`}
              >
                {errorMsg}
              </div>
            )}

            <div className="grid lg:grid-cols-[40%_1fr] gap-4 items-stretch">
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
              {/* A refusal replaces the score block — it never sits beside it.
                  An empty gauge next to "not farmland" reads as a zero. */}
              {refusal.state === 'NOT_FARMLAND' || refusal.state === 'UNOBSERVED' ? (
                <RefusalPanel data={data} />
              ) : (
                <div className="space-y-2.5">
                  <RiskScoreCard
                    data={status === 'SUCCESS' && data ? data : null}
                    placeholder={status !== 'SUCCESS' || !data}
                    statusMessage={
                      status === 'FAILED'
                        ? errorMsg || 'No farmer score — see plot outcomes below.'
                        : loadingMsg || 'Analyzing plots…'
                    }
                  />
                  {status === 'SUCCESS' && <ConfidenceStrip data={data} />}
                </div>
              )}
            </div>

            {/* ── ZONE 2 · EVIDENCE ────────────────────────────────────────
                Why this number, and what we could see. Reader ① (the loan
                officer) usually stops above this line; reader ② starts here.

                Holding level carries no driver captions — the aggregator does
                not emit them, only the per-plot engine does — so none are
                passed rather than invented. */}
            {status === 'SUCCESS' && refusal.state === 'SCORED' && (
              <ScoreWaterfall view={riskView} scopeLabel="holding" />
            )}

            {status === 'SUCCESS' && data?.land_cover && (
              <LandCoverPanel landCover={data.land_cover} />
            )}

            {/* ── ZONE 3 · HOLDING ─────────────────────────────────────── */}
            <div className="grid lg:grid-cols-2 gap-5 items-stretch min-h-[520px]">
              <StreamingFarmList
                rows={streamRows}
                jobId={jobId}
                farmerId={farmerId || String(data?.farmer_id || '')}
                doneCount={doneCounters.n_plots_done ?? 0}
                totalCount={doneCounters.n_plots_total ?? streamRows.length}
                className="min-h-[520px] max-h-[640px]"
                selectedPlotKey={focusedPlotKey}
                onSelectPlot={setFocusedPlotKey}
              />
              <div className="bg-white rounded-xl border border-rule p-3 shadow-sm flex flex-col min-h-[520px] max-h-[640px]">
                <p className="text-xs font-medium text-stone-500 mb-2 px-1 shrink-0">
                  {focusedPlotKey
                    ? `Focused · ${focusedPlotKey}`
                    : `All farms · ${portfolioMapPlots.length} plot(s)`}
                </p>
                <div className="flex-1 min-h-0">
                  <PlotBoundaryMap
                    plots={portfolioMapPlots}
                    selectedPlotKey={focusedPlotKey}
                    onSelectPlot={setFocusedPlotKey}
                    minHeight={480}
                  />
                </div>
              </div>
            </div>

            {/* ── ZONE 4 · PROVENANCE ──────────────────────────────────────
                Collapsed by default. Reader ① never opens it; reader ② opens
                nothing else, and provenance nobody can find is the same as
                provenance that does not exist the first time a decision is
                audited. */}
            {status === 'SUCCESS' && <ProvenanceFooter data={data} />}
          </div>
        )}
      </main>
    </div>
  );
}
