'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../components/providers/AuthProvider';

interface FarmDoc {
  _id: string;
  farmer_name: string;
  phone?: string | null;
  agristack_farmer_id?: string | null;
  location?: {
    state?: { name?: string };
    district?: { name?: string };
    taluka?: { name?: string };
    village?: { name?: string };
  };
  farms?: { farm_name?: string; primary_crop?: string; area_ha?: number }[];
  created_at?: string;
  updated_at?: string;
}

export default function SavedFarmsPage() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const [farmers, setFarmers] = useState<FarmDoc[]>([]);
  const [selected, setSelected] = useState<FarmDoc | null>(null);
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
      <div className="min-h-screen bg-[#F5F2EB] flex items-center justify-center text-stone-500 text-sm">
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
    <div className="min-h-screen bg-[#F5F2EB] text-stone-800">
      <header className="bg-white border-b border-[#E4DFD4] sticky top-0 z-20">
        <div className="flex h-14 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-4 text-sm">
            <Link href="/" className="text-stone-500 hover:text-emerald-700 font-medium">
              Home
            </Link>
            <span className="text-[#E4DFD4]">|</span>
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
          <div className="text-center py-20 border border-dashed border-[#E4DFD4] rounded-xl">
            <p className="text-stone-500 mb-4">No saved farmers yet.</p>
            <Link href="/farmer" className="text-emerald-700 hover:text-emerald-300 text-sm font-medium">
              Start Farmer Journey →
            </Link>
          </div>
        ) : (
          <div className="grid lg:grid-cols-[1fr_320px] gap-6">
            <div className="overflow-x-auto border border-[#E4DFD4] rounded-xl">
              <table className="w-full text-sm text-left">
                <thead className="bg-white text-stone-500 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Name</th>
                    <th className="px-4 py-3 font-semibold">State</th>
                    <th className="px-4 py-3 font-semibold">District</th>
                    <th className="px-4 py-3 font-semibold">Farms</th>
                    <th className="px-4 py-3 font-semibold">Crops</th>
                    <th className="px-4 py-3 font-semibold">Date</th>
                    <th className="px-4 py-3 font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#30363d]">
                  {farmers.map((f) => {
                    const crops = [
                      ...new Set((f.farms || []).map((x) => x.primary_crop).filter(Boolean)),
                    ];
                    const dashId = f.agristack_farmer_id || f._id;
                    return (
                      <tr
                        key={f._id}
                        className={`hover:bg-white/60 cursor-pointer ${
                          selected?._id === f._id ? 'bg-white' : ''
                        }`}
                        onClick={() => setSelected(f)}
                      >
                        <td className="px-4 py-3 font-medium text-stone-900">{f.farmer_name}</td>
                        <td className="px-4 py-3 text-stone-500">{f.location?.state?.name || '—'}</td>
                        <td className="px-4 py-3 text-stone-500">{f.location?.district?.name || '—'}</td>
                        <td className="px-4 py-3 font-mono text-stone-700">{f.farms?.length ?? 0}</td>
                        <td className="px-4 py-3 text-stone-500 max-w-[160px] truncate">
                          {crops.join(', ') || '—'}
                        </td>
                        <td className="px-4 py-3 text-stone-500 text-xs">
                          {f.created_at ? new Date(f.created_at).toLocaleDateString() : '—'}
                        </td>
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <div className="flex flex-wrap gap-2">
                            <Link
                              href={`/farmer?edit=${encodeURIComponent(f._id)}`}
                              className="text-xs font-semibold text-sky-400 hover:text-sky-300"
                            >
                              Edit
                            </Link>
                            <Link
                              href={`/dashboard?farmer_id=${encodeURIComponent(dashId)}`}
                              className="text-xs font-semibold text-emerald-700 hover:text-emerald-300"
                            >
                              Run Assessment
                            </Link>
                            <button
                              type="button"
                              onClick={() => handleDelete(f._id)}
                              className="text-xs text-red-400 hover:text-red-300"
                            >
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <aside className="bg-white border border-[#E4DFD4] rounded-xl p-4 h-fit sticky top-20">
              <h2 className="text-sm font-semibold text-stone-700 mb-3">Details</h2>
              {!selected ? (
                <p className="text-xs text-stone-400">Click a row to view details.</p>
              ) : (
                <div className="space-y-3 text-sm">
                  <div>
                    <p className="text-[10px] text-stone-400 uppercase tracking-wider">Name</p>
                    <p className="text-stone-900 font-medium">{selected.farmer_name}</p>
                  </div>
                  {selected.agristack_farmer_id && (
                    <div>
                      <p className="text-[10px] text-stone-400 uppercase tracking-wider">AgriStack ID</p>
                      <p className="font-mono text-emerald-700 text-xs">{selected.agristack_farmer_id}</p>
                    </div>
                  )}
                  <div>
                    <p className="text-[10px] text-stone-400 uppercase tracking-wider">Location</p>
                    <p className="text-stone-500 text-xs">
                      {[
                        selected.location?.village?.name,
                        selected.location?.taluka?.name,
                        selected.location?.district?.name,
                        selected.location?.state?.name,
                      ]
                        .filter(Boolean)
                        .join(', ') || '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] text-stone-400 uppercase tracking-wider mb-1">Farms</p>
                    <ul className="space-y-1">
                      {(selected.farms || []).map((farm, i) => (
                        <li key={i} className="text-xs text-stone-500">
                          {farm.farm_name || `Farm ${i + 1}`} — {farm.primary_crop || '—'} —{' '}
                          <span className="font-mono">{farm.area_ha?.toFixed?.(3) ?? farm.area_ha ?? '—'} ha</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="flex flex-col gap-2 pt-2">
                    <Link
                      href={`/farmer?edit=${encodeURIComponent(selected._id)}`}
                      className="text-center text-xs font-semibold bg-[#21262d] hover:bg-[#30363d] text-sky-400 py-2 rounded-lg"
                    >
                      Edit in wizard
                    </Link>
                    <Link
                      href={`/dashboard?farmer_id=${encodeURIComponent(selected.agristack_farmer_id || selected._id)}`}
                      className="text-center text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white py-2 rounded-lg"
                    >
                      Run Assessment
                    </Link>
                  </div>
                </div>
              )}
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
