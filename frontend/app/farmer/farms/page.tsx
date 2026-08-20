'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../components/providers/AuthProvider';
import { FarmerDetailPanel, FarmerFarmsTable } from '../../components/farmers/FarmerFarmsTable';
import type { FarmerListItem } from '../../lib/farmerLocation';

export default function SavedFarmsPage() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const [farmers, setFarmers] = useState<FarmerListItem[]>([]);
  const [selected, setSelected] = useState<FarmerListItem | null>(null);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setFetching(true);
    try {
      const res = await fetch('/api/farms');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to load');
      setFarmers(data.farmers || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setFetching(false);
    }
  };

  useEffect(() => {
    if (!loading && user) load();
  }, [loading, user]);

  if (loading) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
        Loading…
      </div>
    );
  }

  if (!user) {
    router.replace('/login?callbackUrl=/farmer/farms');
    return null;
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this farmer and all farm boundaries?')) return;
    const res = await fetch(`/api/farms/${id}`, { method: 'DELETE' });
    if (res.ok) {
      setFarmers((prev) => prev.filter((f) => f._id !== id));
      if (selected?._id === id) setSelected(null);
    }
  };

  return (
    <div className="min-h-screen bg-paper text-stone-800">
      <header className="bg-white border-b border-rule sticky top-0 z-20">
        <div className="flex h-14 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-4 text-sm">
            <Link href="/" className="text-stone-500 hover:text-emerald-700 font-medium">
              Home
            </Link>
            <span className="text-rule">|</span>
            <h1 className="font-bold text-stone-900">Saved Farms</h1>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <Link
              href="/farmer"
              className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold px-3 py-1.5 rounded-lg"
            >
              + New Farmer
            </Link>
            <button onClick={() => logout()} className="text-red-400 hover:text-red-300">
              Logout
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {error && (
          <div className="mb-4 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
            {error}
          </div>
        )}

        {fetching ? (
          <p className="text-sm text-stone-500">Loading farms…</p>
        ) : farmers.length === 0 ? (
          <div className="text-center py-20 border border-dashed border-rule rounded-xl">
            <p className="text-stone-500 mb-4">No saved farmers yet.</p>
            <Link href="/farmer" className="text-emerald-700 hover:text-emerald-800 text-sm font-medium">
              Start Farmer Journey →
            </Link>
          </div>
        ) : (
          <div className="grid lg:grid-cols-[1fr_320px] gap-6">
            <FarmerFarmsTable
              farmers={farmers}
              selectedId={selected?._id}
              onSelect={setSelected}
              onDelete={handleDelete}
              showManageActions
            />
            <FarmerDetailPanel
              selected={selected}
              onDelete={handleDelete}
              showManageActions
            />
          </div>
        )}
      </main>
    </div>
  );
}
