'use client';

import dynamic from 'next/dynamic';
import type { MapPlot, MeasuredFootprint, NdviSeries } from './PlotBoundaryMapInner';
import type { GeospatialPrep, Footprint } from '../../types/assessment';

const PlotBoundaryMapInner = dynamic(() => import('./PlotBoundaryMapInner'), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center text-sm text-stone-500 min-h-[320px] h-full bg-paper rounded-xl border border-rule">
      Loading map…
    </div>
  ),
});

export type { MapPlot, MeasuredFootprint, NdviSeries };

/**
 * Resolve the measured footprint from what the pipeline stamped on the
 * assessment. `footprint.geometry_substituted` is the verdict;
 * `geospatial_prep.buffer_km_used` is the radius that makes it drawable.
 * Returns null when the declared boundary IS what we measured — the common
 * case, which needs no extra marks on the map.
 */
export function measuredFootprintOf(
  footprint: Footprint | null | undefined,
  prep: GeospatialPrep | null | undefined
): MeasuredFootprint | null {
  if (!footprint?.geometry_substituted) return null;
  return {
    substituted: true,
    bufferKm: prep?.buffer_km_used ?? null,
    source: footprint.geometry_source ?? prep?.geometry_source ?? null,
  };
}

export function PlotBoundaryMap({
  geometry,
  centroid,
  label,
  plots,
  selectedPlotKey = null,
  onSelectPlot,
  minHeight = 320,
  measuredFootprint = null,
  ndvi = null,
}: {
  geometry?: { type?: string; coordinates?: unknown } | null;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
  /** When set, draws all farm boundaries (portfolio / overview map). */
  plots?: MapPlot[];
  selectedPlotKey?: string | null;
  onSelectPlot?: (plotKey: string) => void;
  minHeight?: number;
  /** Draws the substituted footprint against the declared boundary. */
  measuredFootprint?: MeasuredFootprint | null;
  /** Parcel-mean NDVI per bin — enables the date scrubber. Not a raster. */
  ndvi?: NdviSeries | null;
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
      measuredFootprint={measuredFootprint}
      ndvi={ndvi}
    />
  );
}
