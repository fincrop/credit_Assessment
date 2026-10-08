'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../components/providers/AuthProvider';
import { CLUSTER_PAIRS } from './catalog';

interface RcSummary {
  id: string;
  name: string;
  district: string;
  taluka: string;
  crop: string;
  villages: number;
  areaHa: number;
  sowing: string;
  classifiedVillages: number;
  running: number;
  failed: number;
  uploadedAreaHa: number;
  declaredCropAreaHa: number;
  othersAreaHa: number;
  declaredShare: number | null;
  meanConfidence: number | null;
  fieldCount: number;
  villageRuns: { name: string; job_id: string; area_ha: number }[];
  monitoredVillages: number;
  stress: Record<string, number>;
  sowingEarliest: string | null;
  sowingLatest: string | null;
  meanYieldTHa: number | null;
}

interface Payload {
  pair: { id: string; district: string; crop: string; sowing: string };
  rcs: RcSummary[];
}

function pct(v: number | null): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`;
}

function ha(v: number): string {
  return v.toLocaleString('en-IN', { maximumFractionDigits: 1 });
}

function stressLine(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  if (!entries.length) return '—';
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${k} ${Math.round((n / total) * 100)}%`)
    .join(' · ');
}

function Column({ rc }: { rc: RcSummary }) {
  const rows: [string, string][] = [
    ['Declared villages', String(rc.villages)],
    ['Villages classified', `${rc.classifiedVillages} of ${rc.villages}`],
    ['Still running', String(rc.running)],
    ['Failed', String(rc.failed)],
    ['Declared area', `${ha(rc.areaHa)} ha`],
    ['Area uploaded', `${ha(rc.uploadedAreaHa)} ha`],
    ['Named as ' + rc.crop, `${ha(rc.declaredCropAreaHa)} ha`],
    ['Share of classified area', pct(rc.declaredShare)],
    ['Shown as Others', `${ha(rc.othersAreaHa)} ha`],
    ['Fields', rc.fieldCount ? rc.fieldCount.toLocaleString('en-IN') : '—'],
    ['Mean confidence', rc.meanConfidence == null ? '—' : rc.meanConfidence.toFixed(2)],
    ['Villages monitored', String(rc.monitoredVillages)],
    ['Sowing window seen', rc.sowingEarliest ? `${rc.sowingEarliest} → ${rc.sowingLatest || rc.sowingEarliest}` : '—'],
    ['Stress', stressLine(rc.stress)],
    ['Mean yield', rc.meanYieldTHa == null ? '—' : `${rc.meanYieldTHa.toFixed(2)} t/ha`],
  ];
  return (
    <section className="rounded-2xl border border-rule bg-white p-5 min-w-0">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-stone-500">
        {rc.district} · {rc.taluka}
      </p>
      <h2 className="text-xl font-bold text-stone-900 mt-1">{rc.name}</h2>
      <p className="text-xs text-stone-500 mt-1">Sowing on the sheet: {rc.sowing}</p>
      <dl className="mt-4 divide-y divide-stone-100">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 py-2">
            <dt className="text-xs text-stone-500">{label}</dt>
            <dd className="text-sm font-medium text-stone-900 text-right">{value}</dd>
          </div>
        ))}
      </dl>
      {rc.villageRuns.length > 0 && (
        <ul className="mt-3 space-y-1">
          {rc.villageRuns.map((v) => (
            <li key={v.job_id}>
              <Link
                href={`/classification?job=${encodeURIComponent(v.job_id)}`}
                className="text-xs text-emerald-800 hover:underline"
              >
                {v.name}
                <span className="text-stone-400"> · {ha(v.area_ha)} ha</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function ClusterPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [pairId, setPairId] = useState(CLUSTER_PAIRS[0].id);
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    if (!loading && !user) router.replace('/login');
  }, [loading, user, router]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    setBusy(true);
    setError(null);
    (async () => {
      try {
        const res = await fetch(`/api/clusters?pair=${encodeURIComponent(pairId)}`, { credentials: 'include' });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error || 'Could not load the comparison');
        if (!cancelled) setData(body as Payload);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the comparison');
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user, pairId]);

  if (loading || !user) {
    return <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">Loading…</div>;
  }

  const pair = CLUSTER_PAIRS.find((p) => p.id === pairId) || CLUSTER_PAIRS[0];
  const [left, right] = data?.rcs || [];

  return (
    <div className="min-h-screen bg-paper text-stone-800">
      <header className="page-shell flex items-center justify-between py-2 border-b border-rule">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-stone-500 hover:text-emerald-700 text-sm">← Home</Link>
          <div className="h-4 w-px bg-rule" />
          <div>
            <h1 className="text-sm font-bold text-stone-900 tracking-tight">Cluster analysis</h1>
            <p className="text-[10px] text-stone-500 font-mono">SBI General · two revenue circles, one crop</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/classification" className="text-xs text-stone-500 hover:text-emerald-700">Classification</Link>
          <Link href="/monitoring" className="text-xs text-stone-500 hover:text-emerald-700">Monitoring</Link>
        </div>
      </header>

      <main className="page-shell py-6 max-w-5xl">
        <div className="flex flex-wrap gap-2">
          {CLUSTER_PAIRS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setPairId(p.id)}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${
                pairId === p.id
                  ? 'border-emerald-600 bg-emerald-600 text-white'
                  : 'border-rule bg-white text-stone-700 hover:border-emerald-400'
              }`}
            >
              {p.district} · {p.crop}
            </button>
          ))}
        </div>

        <p className="mt-4 text-sm text-stone-600 leading-relaxed">
          {pair.district} {pair.crop}. Sowing on the sheet is {pair.sowing}. Each village is classified on its own.
          Tag it with the revenue circle on the classification form and it appears here.
        </p>
        {pair.crop === 'Soybean' && (
          <p className="mt-2 text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 leading-relaxed">
            Soybean names at the start of October were right on about 42% of the frozen test.
            The shares below use those names. A November pass is the one that reached about 85%.
          </p>
        )}
        {pair.crop === 'Cotton' && (
          <p className="mt-2 text-sm text-stone-700 bg-white border border-rule rounded-xl px-3 py-2 leading-relaxed">
            Cotton at the start of October was 85% recall on surveyed boundaries. These runs use delineated
            village outlines, which overlap a true field less tightly than that test.
          </p>
        )}

        {error && <p className="mt-4 text-sm text-red-800">{error}</p>}
        {busy && <p className="mt-6 text-sm text-stone-500">Loading villages…</p>}

        {!busy && left && right && (
          <>
            <div className="mt-6 grid md:grid-cols-2 gap-4">
              <Column rc={left} />
              <Column rc={right} />
            </div>
            <section className="mt-4 rounded-2xl border border-rule bg-white px-5 py-4">
              <h3 className="text-sm font-bold text-stone-900">Difference, {left.name} minus {right.name}</h3>
              <dl className="mt-2 grid sm:grid-cols-3 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-stone-500">{pair.crop} share</dt>
                  <dd className="font-medium">
                    {left.declaredShare == null || right.declaredShare == null
                      ? '—'
                      : `${Math.round((left.declaredShare - right.declaredShare) * 100)} points`}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-stone-500">Mean confidence</dt>
                  <dd className="font-medium">
                    {left.meanConfidence == null || right.meanConfidence == null
                      ? '—'
                      : (left.meanConfidence - right.meanConfidence).toFixed(2)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-stone-500">Villages still to classify</dt>
                  <dd className="font-medium">
                    {left.villages - left.classifiedVillages} and {right.villages - right.classifiedVillages}
                  </dd>
                </div>
              </dl>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
