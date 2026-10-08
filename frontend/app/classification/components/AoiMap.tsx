'use client';

import dynamic from 'next/dynamic';
import type { ReactNode } from 'react';
import type { AreaOfInterest, ClassStat } from '../types';
import type { RasterOverlaySpec } from './AoiMapInner';

export type { RasterOverlaySpec };

/** Leaflet touches `window` at import time, so the map is client-only. */
const AoiMapInner = dynamic(() => import('./AoiMapInner'), {
  ssr: false,
  loading: () => (
    <div className="classification-map h-full min-h-[280px] flex items-center justify-center text-sm text-stone-400">
      Loading map…
    </div>
  ),
});

interface Props {
  areas: AreaOfInterest[];
  onAreasChange: (areas: AreaOfInterest[]) => void;
  resultLayer?: GeoJSON.FeatureCollection | null;
  onFieldClick?: (props: Record<string, unknown>) => void;
  readOnly?: boolean;
  heightClass?: string;
  legend?: ClassStat[] | null;
  toolbar?: ReactNode;
  rasterOverlay?: RasterOverlaySpec | null;
  overlay?: ReactNode;
  selectedFieldId?: string | null;
}

export function AoiMap(props: Props) {
  return <AoiMapInner {...props} />;
}
