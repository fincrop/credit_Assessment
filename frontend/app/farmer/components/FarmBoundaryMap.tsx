'use client';

import dynamic from 'next/dynamic';
import type { FarmPolygon } from '../types';

const FarmBoundaryMapInner = dynamic(() => import('./FarmBoundaryMapInner'), {
  ssr: false,
  loading: () => (
    <div className="farm-map-container flex items-center justify-center text-sm text-stone-500">
      Loading map…
    </div>
  ),
});

interface Props {
  farms: FarmPolygon[];
  onFarmsChange: (farms: FarmPolygon[]) => void;
  mapCenter?: { lat: number; lng: number } | null;
  /** Fired when user finishes drawing a polygon (before Save Farm). */
  onBoundaryDrawn?: (centroid: { lat: number; lng: number }) => void;
}

export function FarmBoundaryMap(props: Props) {
  return <FarmBoundaryMapInner {...props} />;
}
