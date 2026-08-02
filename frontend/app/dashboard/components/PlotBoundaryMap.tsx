'use client';

import dynamic from 'next/dynamic';

const PlotBoundaryMapInner = dynamic(() => import('./PlotBoundaryMapInner'), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center text-sm text-stone-500 min-h-[320px] bg-[#F5F2EB] rounded-xl border border-[#E4DFD4]">
      Loading map…
    </div>
  ),
});

export function PlotBoundaryMap({
  geometry,
  centroid,
  label,
}: {
  geometry?: { type?: string; coordinates?: unknown } | null;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
}) {
  return (
    <PlotBoundaryMapInner geometry={geometry} centroid={centroid} label={label} />
  );
}
