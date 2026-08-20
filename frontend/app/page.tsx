'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from './components/providers/AuthProvider';
import { LocationPortfolio } from './components/farmers/LocationPortfolio';
import type { FarmerListItem } from './lib/farmerLocation';

export default function HomePage() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const [farmers, setFarmers] = useState<FarmerListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  useEffect(() => {
    if (!loading && !user) {
      router.replace('/login');
    }
  }, [loading, user, router]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      setHistoryLoading(true);
      try {
        const res = await fetch('/api/farms', { credentials: 'include' });
        const data = await res.json();
        if (!cancelled && res.ok && Array.isArray(data.farmers)) {
          setFarmers(data.farmers);
        }
      } catch {
        if (!cancelled) setFarmers([]);
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  if (loading || !user) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
        Loading…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-paper text-stone-800 relative overflow-hidden">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 80% 50% at 20% -10%, rgba(34,197,94,0.18), transparent 55%), radial-gradient(ellipse 60% 40% at 90% 10%, rgba(56,189,248,0.08), transparent 50%), linear-gradient(180deg, #F5F2EB 0%, #EFEBE3 100%)',
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.035]"
        style={{
          backgroundImage:
            'url("data:image/svg+xml,%3Csvg width=\'60\' height=\'60\' viewBox=\'0 0 60 60\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cg fill=\'none\' fill-rule=\'evenodd\'%3E%3Cg fill=\'%23ffffff\' fill-opacity=\'1\'%3E%3Cpath d=\'M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z\'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")',
        }}
      />

      <header className="relative z-10 flex items-center justify-between px-6 py-5 max-w-7xl mx-auto w-full">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-emerald-500 flex items-center justify-center font-bold text-paper text-sm">
            A
          </div>
          <div>
            <p className="text-sm font-bold text-stone-900 tracking-tight">AgriCredit</p>
            <p className="text-[10px] text-stone-500 font-mono">Platform Home</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-700 flex items-center justify-center text-xs font-bold text-white">
              {user.name?.[0]?.toUpperCase() || 'U'}
            </div>
            <span className="text-sm text-stone-700 hidden sm:inline">{user.name}</span>
          </div>
          <button
            onClick={() => logout()}
            className="text-sm text-red-400 hover:text-red-300 px-3 py-1.5 rounded-lg hover:bg-red-500/10 transition-colors"
          >
            Logout
          </button>
        </div>
      </header>

      <main className="relative z-10 max-w-7xl mx-auto px-6 pt-10 pb-20">
        <div className="mb-12 max-w-2xl animate-slide-in">
          <h1 className="text-3xl sm:text-4xl font-bold text-stone-900 tracking-tight mb-3">
            Choose your journey
          </h1>
          <p className="text-stone-500 text-base leading-relaxed">
            Pull official AgriStack land records by Farmer ID and run a credit assessment, or register a
            farm yourself for an AI-powered score.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          <Link
            href="/agristack/connect?next=/dashboard"
            className="group relative rounded-2xl border border-rule bg-white/80 backdrop-blur-md p-8 hover:border-emerald-400 hover:bg-emerald-50 transition-all duration-300 animate-slide-in"
            style={{ animationDelay: '0.05s' }}
          >
            <div
              className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.06)' }}
            />
            <div className="w-12 h-12 rounded-xl bg-sky-500/15 border border-sky-500/25 flex items-center justify-center mb-5 text-2xl">
              🛰️
            </div>
            <h2 className="text-xl font-bold text-stone-900 mb-2 group-hover:text-emerald-300 transition-colors">
              AgriStack Assessment
            </h2>
            <p className="text-sm text-stone-500 leading-relaxed mb-6">
              Sign in with your AgriStack credentials, then enter a Farmer ID to fetch land records
              and choose plots to score.
            </p>
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-sky-400 group-hover:gap-3 transition-all">
              Connect AgriStack
              <span aria-hidden>→</span>
            </span>
          </Link>

          <Link
            href="/farmer"
            className="group relative rounded-2xl border border-rule bg-white/80 backdrop-blur-md p-8 hover:border-emerald-400 hover:bg-emerald-50 transition-all duration-300 animate-slide-in"
            style={{ animationDelay: '0.12s' }}
          >
            <div
              className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ boxShadow: 'inset 0 1px 0 0 rgba(255,255,255,0.06)' }}
            />
            <div className="w-12 h-12 rounded-xl bg-emerald-500/15 border border-emerald-500/25 flex items-center justify-center mb-5 text-2xl">
              🌾
            </div>
            <h2 className="text-xl font-bold text-stone-900 mb-2 group-hover:text-emerald-300 transition-colors">
              Farmer Assessment Journey
            </h2>
            <p className="text-sm text-stone-500 leading-relaxed mb-6">
              Add your farm boundaries, crops, and get an AI-powered credit assessment — no AgriStack
              account needed.
            </p>
            <span className="inline-flex items-center gap-2 text-sm font-semibold text-emerald-700 group-hover:gap-3 transition-all">
              Start farmer journey
              <span aria-hidden>→</span>
            </span>
          </Link>
        </div>

        <section className="mt-14 animate-slide-in" style={{ animationDelay: '0.18s' }}>
          <div className="flex items-end justify-between gap-4 mb-5">
            <div>
              <h2 className="text-xl font-bold text-stone-900 tracking-tight">Portfolio summary</h2>
              <p className="text-sm text-stone-500 mt-1">
                Filter by state, then district. Widgets and the location chart sit on the left;
                matching farms on the right.
              </p>
            </div>
            <Link
              href="/farmer/farms"
              className="text-sm font-medium text-emerald-700 hover:text-emerald-800 shrink-0"
            >
              Manage all →
            </Link>
          </div>

          {historyLoading ? (
            <p className="text-sm text-stone-500 py-8">Loading your history…</p>
          ) : farmers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-rule bg-white/50 px-6 py-10 text-center">
              <p className="text-stone-600 text-sm">
                No assessments yet — start Farmer Assessment or connect AgriStack.
              </p>
            </div>
          ) : (
            <LocationPortfolio farmers={farmers} />
          )}
        </section>

        <div className="mt-10 flex flex-wrap gap-4 text-sm animate-slide-in" style={{ animationDelay: '0.2s' }}>
          <Link href="/dashboard" className="text-stone-500 hover:text-emerald-700 transition-colors">
            Assessment Dashboard →
          </Link>
          <Link href="/farmer/farms" className="text-stone-500 hover:text-emerald-700 transition-colors">
            Saved Farms →
          </Link>
        </div>
      </main>
    </div>
  );
}
