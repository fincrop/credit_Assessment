'use client';

import { Suspense, useEffect, useRef, useState, useCallback } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '../components/providers/AuthProvider';
import { FarmerInfoForm } from './components/FarmerInfoForm';
import { LocationSelector } from './components/LocationSelector';
import { FarmBoundaryMap } from './components/FarmBoundaryMap';
import { FarmCard } from './components/FarmCard';
import { GeospatialUpload } from './components/GeospatialUpload';
import { fillLocationFromCoords } from './lib/fillLocationFromCoords';
import type { FarmerIdentity, FarmerLocation, FarmPolygon } from './types';
import { FARM_COLORS } from './types';

export default function FarmerJourneyPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
          Loading…
        </div>
      }
    >
      <FarmerJourneyContent />
    </Suspense>
  );
}

function FarmerJourneyContent() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const editId = (searchParams.get('edit') || '').trim();

  const [submitting, setSubmitting] = useState(false);
  const [loadingEdit, setLoadingEdit] = useState(!!editId);
  const [error, setError] = useState<string | null>(null);
  const [locHint, setLocHint] = useState<string | null>(null);
  const [farmerDocId, setFarmerDocId] = useState<string | null>(editId || null);
  const autoFillKey = useRef<string | null>(null);

  const [identity, setIdentity] = useState<FarmerIdentity>({
    farmer_name: '',
    phone: '',
    language: 'English',
    agristack_farmer_id: '',
  });

  const [location, setLocation] = useState<FarmerLocation>({
    state: null,
    district: null,
    taluka: null,
    village: null,
    katha_number: '',
    mapCenter: null,
  });

  const locationRef = useRef(location);
  locationRef.current = location;

  const [farms, setFarms] = useState<FarmPolygon[]>([]);

  useEffect(() => {
    if (!editId) return;
    let cancelled = false;
    (async () => {
      setLoadingEdit(true);
      try {
        const res = await fetch(`/api/farms/${encodeURIComponent(editId)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to load farmer');
        if (cancelled) return;
        const f = data.farmer;
        setFarmerDocId(f._id);
        setIdentity({
          farmer_name: f.farmer_name || '',
          phone: f.phone || '',
          language: f.language || 'English',
          agristack_farmer_id: f.agristack_farmer_id || '',
        });
        const loc = f.location || {};
        const firstFarm = (f.farms || [])[0];
        setLocation({
          state: loc.state || null,
          district: loc.district || null,
          taluka: loc.taluka || null,
          village: loc.village || null,
          katha_number: loc.katha_number || '',
          mapCenter: firstFarm?.centroid || null,
        });
        setFarms(
          (f.farms || []).map(
            (farm: FarmPolygon & { color?: string }, i: number): FarmPolygon => ({
              farm_id: farm.farm_id || crypto.randomUUID(),
              farm_name: farm.farm_name || `Plot ${i + 1}`,
              farm_number: farm.farm_number || '',
              boundary: farm.boundary,
              area_ha: farm.area_ha || 0,
              centroid: farm.centroid || { lat: 20.5, lng: 78.9 },
              primary_crop: farm.primary_crop || 'Rice',
              sowing_date: farm.sowing_date || '',
              color: farm.color || FARM_COLORS[i % FARM_COLORS.length],
            })
          )
        );
        if (firstFarm?.centroid) {
          autoFillKey.current = `${firstFarm.centroid.lat.toFixed(5)},${firstFarm.centroid.lng.toFixed(5)}`;
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Load failed');
      } finally {
        if (!cancelled) setLoadingEdit(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [editId]);

  // Auto-fill location when farms are saved or uploaded
  const applyLocationFromCentroid = useCallback(
    async (centroid: { lat: number; lng: number }, source: 'draw' | 'upload') => {
      const key = `${centroid.lat.toFixed(5)},${centroid.lng.toFixed(5)}`;
      if (autoFillKey.current === key) return;

      setLocHint(
        source === 'draw'
          ? 'Detecting location from drawn boundary…'
          : 'Detecting location from uploaded boundaries…'
      );

      const filled = await fillLocationFromCoords(centroid.lat, centroid.lng, {
        ...locationRef.current,
        mapCenter: centroid,
      });
      autoFillKey.current = key;
      setLocation(filled);
      const label = [
        filled.village?.name,
        filled.taluka?.name,
        filled.district?.name,
        filled.state?.name,
      ]
        .filter(Boolean)
        .join(', ');
      setLocHint(
        label
          ? `Location auto-filled from ${source === 'draw' ? 'farm boundary' : 'upload'}: ${label}`
          : `Map centered at ${centroid.lat.toFixed(4)}, ${centroid.lng.toFixed(4)} — refine location if needed.`
      );
    },
    []
  );

  useEffect(() => {
    if (!farms.length) return;
    const c = farms[0]?.centroid;
    if (!c) return;
    void applyLocationFromCentroid(c, 'upload');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [farms]);

  const handleBoundaryDrawn = useCallback(
    (centroid: { lat: number; lng: number }) => {
      void applyLocationFromCentroid(centroid, 'draw');
    },
    [applyLocationFromCentroid]
  );

  const handleFarmsChange = (next: FarmPolygon[]) => {
    setFarms(next);
  };

  if (loading || loadingEdit) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
        {loadingEdit ? 'Loading farmer…' : 'Loading…'}
      </div>
    );
  }

  if (!user) {
    router.replace('/login?callbackUrl=/farmer');
    return null;
  }

  const hasBoundaries = farms.length > 0;
  // Identity + location are optional once boundaries exist (draw or upload)
  const canSubmit = hasBoundaries;

  const handleSubmit = async () => {
    if (!canSubmit) {
      setError('Add at least one farm boundary (draw on the map or upload a file).');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        farmer_id: farmerDocId || undefined,
        farmer_name:
          identity.farmer_name.trim() ||
          `Farmer ${farms[0]?.farm_name || 'plot'}`.slice(0, 80),
        phone: identity.phone.trim() || null,
        language: identity.language,
        agristack_farmer_id: identity.agristack_farmer_id.trim() || null,
        location: {
          state: location.state,
          district: location.district,
          taluka: location.taluka,
          village: location.village,
          katha_number: location.katha_number || null,
        },
        farms: farms.map((f) => ({
          farm_id: f.farm_id,
          farm_name: f.farm_name,
          farm_number: f.farm_number || null,
          boundary: f.boundary,
          area_ha: f.area_ha,
          centroid: f.centroid,
          primary_crop: f.primary_crop,
          sowing_date: f.sowing_date || null,
        })),
        historical_data: [],
        farmer_benefits: {
          pm_kisan_enrolled: false,
          has_crop_insurance: false,
        },
        irrigation_type: null,
        soil_type: null,
        notes: null,
      };

      const res = await fetch('/api/farms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

      const dashId =
        data.pipeline_farmer_id ||
        identity.agristack_farmer_id.trim() ||
        data.farmer_id;
      router.push(`/dashboard?farmer_id=${encodeURIComponent(dashId)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-paper text-stone-800">
      <header className="bg-paper-raised/95 backdrop-blur border-b border-rule sticky top-0 z-20">
        <div className="flex h-14 items-center px-6 max-w-7xl mx-auto w-full justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-stone-500 hover:text-emerald-700 text-sm font-medium">
              Home
            </Link>
            <span className="text-rule">|</span>
            <h1 className="text-sm font-bold text-stone-900">
              {farmerDocId ? 'Edit Farmer' : 'Farmer Assessment Journey'}
            </h1>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <Link href="/farmer/farms" className="text-stone-500 hover:text-emerald-700">
              Saved Farms
            </Link>
            <button onClick={() => logout()} className="text-red-600 hover:text-red-500">
              Logout
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8 pb-24">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
            {error}
          </div>
        )}
        {locHint && (
          <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm rounded-lg px-4 py-3">
            {locHint}
          </div>
        )}

        {/* Identity — single horizontal row */}
        <section className="farmer-section">
          <h2 className="text-lg font-bold text-stone-900 mb-3">Identity</h2>
          <FarmerInfoForm
            value={identity}
            onChange={setIdentity}
            optional={hasBoundaries}
          />
        </section>

        {/* Location + Farm boundaries side by side */}
        <div className="grid lg:grid-cols-[minmax(280px,360px)_1fr] gap-6 items-start">
          <section className="farmer-section">
            <h2 className="text-lg font-bold text-stone-900 mb-1">Location</h2>
            <p className="text-sm text-stone-500 mb-4">
              Administrative details. Auto-filled when you draw, enter coordinates, or upload a
              geospatial file.
            </p>
            <LocationSelector
              value={location}
              onChange={setLocation}
              optional={hasBoundaries}
            />
          </section>

          <section className="farmer-section min-w-0">
            <h2 className="text-lg font-bold text-stone-900 mb-1">Farm boundaries</h2>
            <p className="text-sm text-stone-500 mb-4">
              Draw polygons on the satellite map (toolbar), or add coordinates manually. Name each
              plot after drawing.
            </p>
            <div className="space-y-3 mb-4">
              {farms.length === 0 ? (
                <p className="text-xs text-stone-500 border border-dashed border-rule rounded-lg p-3">
                  No farms yet. Draw a polygon on the map, then save it — or upload a file below.
                </p>
              ) : (
                <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  {farms.map((f) => (
                    <FarmCard
                      key={f.farm_id}
                      farm={f}
                      onDelete={() => setFarms(farms.filter((x) => x.farm_id !== f.farm_id))}
                      onEdit={(updated) =>
                        setFarms(farms.map((x) => (x.farm_id === updated.farm_id ? updated : x)))
                      }
                    />
                  ))}
                </div>
              )}
            </div>
            <FarmBoundaryMap
              farms={farms}
              onFarmsChange={handleFarmsChange}
              mapCenter={location.mapCenter}
              onBoundaryDrawn={handleBoundaryDrawn}
            />
          </section>
        </div>

        {/* Geospatial upload */}
        <section className="farmer-section">
          <h2 className="text-lg font-bold text-stone-900 mb-1">Upload geospatial file</h2>
          <p className="text-sm text-stone-500 mb-4">
            Import GeoJSON, KML/KMZ, or a shapefile ZIP. When you upload, Identity and Location
            become optional and location is filled from the file coordinates.
          </p>
          <GeospatialUpload farms={farms} onFarmsChange={handleFarmsChange} />
        </section>

        <div className="flex items-center justify-end gap-3 pt-2">
          <Link
            href="/farmer/farms"
            className="px-4 py-2.5 text-sm text-stone-500 hover:text-stone-800"
          >
            Cancel
          </Link>
          <button
            type="button"
            disabled={submitting || !canSubmit}
            onClick={handleSubmit}
            className="bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-300 disabled:text-stone-500 text-white font-semibold px-5 py-2.5 rounded-lg text-sm shadow-sm"
          >
            {submitting
              ? 'Saving…'
              : farmerDocId
                ? 'Update & go to Dashboard'
                : 'Save & go to Dashboard'}
          </button>
        </div>
      </main>
    </div>
  );
}
