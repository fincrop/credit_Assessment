'use client';

import dynamic from 'next/dynamic';
import type { MapPlot } from './PlotBoundaryMapInner';

const PlotBoundaryMapInner = dynamic(() => import('./PlotBoundaryMapInner'), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center text-sm text-stone-500 min-h-[320px] h-full bg-[#F5F2EB] rounded-xl border border-[#E4DFD4]">
      Loading map…
    </div>
  ),
});

export type { MapPlot };

export function PlotBoundaryMap({
  geometry,
  centroid,
  label,
  plots,
  selectedPlotKey = null,
  onSelectPlot,
  minHeight = 320,
}: {
  geometry?: { type?: string; coordinates?: unknown } | null;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
  /** When set, draws all farm boundaries (portfolio / overview map). */
  plots?: MapPlot[];
  selectedPlotKey?: string | null;
  onSelectPlot?: (plotKey: string) => void;
  minHeight?: number;
}) {
  return (
    <PlotBoundaryMapInner
      geometry={geometry}
      centroid={centroid}
      label={label}
      plots={plots}
      selectedPlotKey={selectedPlotKey}
      onSelectPlot={onSelectPlot}
      minHeight={minHeight}
    />
  );
}
