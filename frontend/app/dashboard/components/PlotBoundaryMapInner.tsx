'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { MAP_COLORS, parcelStyle } from '../../lib/mapStyle';
import { vegetationAt } from '../../lib/vizPalette';

type Geom = { type?: string; coordinates?: unknown } | null | undefined;

/**
 * The footprint the score was actually measured over, when it differs from
 * the declared boundary. `bufferKm` is the real radius from
 * `geospatial_prep.buffer_km_used`, so what gets drawn is the geometry the
 * pipeline used — not an illustration of the idea.
 */
export type MeasuredFootprint = {
  substituted: boolean;
  bufferKm?: number | null;
  source?: string | null;
};

/**
 * Measured NDVI per observation bin, for tinting the parcel over time.
 *
 * This is a PARCEL MEAN — one value for the whole polygon per bin — not a
 * raster. The pipeline produces no imagery layers (no tile service, no
 * GeoTIFF, no thumbnails), so a pixel-level overlay would have to be
 * invented. Tinting the polygon by its own measured mean is the honest
 * version of the same idea, and the UI says which it is: implying
 * within-field detail we do not have would be the same fabrication the
 * trajectory chart refuses, wearing a different costume.
 */
export type NdviSeries = {
  dates: string[];
  values: (number | null)[];
};

export type MapPlot = {
  geometry?: Geom;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
  plot_key?: string;
};

type LayerWithKey = L.Layer & { __plotKey?: string };

const STYLE_DIM = {
  color: MAP_COLORS.boundaryFill,
  weight: 1.5,
  fillColor: MAP_COLORS.boundary,
  fillOpacity: 0.15,
};
const STYLE_ACTIVE = {
  color: MAP_COLORS.warnFill,
  weight: 3,
  fillColor: MAP_COLORS.warn,
  fillOpacity: 0.4,
};
const STYLE_SINGLE = {
  color: MAP_COLORS.boundary,
  weight: 2,
  fillColor: MAP_COLORS.boundary,
  fillOpacity: 0.25,
};

function centroidOf(geom: Geom): { lat: number; lng: number } | null {
  if (!geom?.coordinates) return null;
  try {
    const coords =
      geom.type === 'Polygon'
        ? (geom.coordinates as number[][][])[0]
        : geom.type === 'MultiPolygon'
          ? (geom.coordinates as number[][][][])[0][0]
          : null;
    if (!coords?.length) return null;
    let sx = 0;
    let sy = 0;
    for (const [x, y] of coords) {
      sx += Number(x);
      sy += Number(y);
    }
    return { lng: sx / coords.length, lat: sy / coords.length };
  } catch {
    return null;
  }
}

function isMapAlive(map: L.Map | null): map is L.Map {
  if (!map) return false;
  try {
    const el = map.getContainer();
    return Boolean(el?.isConnected);
  } catch {
    return false;
  }
}

function safeFitBounds(
  map: L.Map,
  bounds: L.LatLngBounds,
  opts: L.FitBoundsOptions
) {
  if (!isMapAlive(map) || !bounds.isValid()) return;
  try {
    map.fitBounds(bounds, opts);
  } catch {
    /* leaflet mid-teardown */
  }
}

function safeInvalidate(map: L.Map | null) {
  if (!isMapAlive(map)) return;
  try {
    map.invalidateSize({ animate: false });
  } catch {
    /* leaflet mid-teardown (_leaflet_pos) */
  }
}

function applySelectionStyles(group: L.LayerGroup, selectedKey: string | null, multi: boolean) {
  group.eachLayer((raw) => {
    const layer = raw as LayerWithKey;
    const active = Boolean(selectedKey && layer.__plotKey === selectedKey);
    if (layer instanceof L.CircleMarker) {
      layer.setStyle({
        radius: active ? 11 : 8,
        color: active ? MAP_COLORS.warn : MAP_COLORS.boundary,
        fillColor: active ? MAP_COLORS.warnFill : MAP_COLORS.boundary,
        fillOpacity: 0.85,
        weight: active ? 3 : 2,
      });
      return;
    }
    if (layer instanceof L.GeoJSON) {
      layer.setStyle(
        multi ? (active ? STYLE_ACTIVE : STYLE_DIM) : STYLE_SINGLE
      );
    }
  });
}

function LegendRow({
  color,
  label,
  dashed,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5 text-[10px] text-ink-2 leading-tight mt-0.5 first:mt-0">
      {dashed ? (
        <svg width="12" height="8" className="shrink-0">
          <line x1="0" y1="4" x2="12" y2="4" stroke={color} strokeWidth="2" strokeDasharray="3 2" />
        </svg>
      ) : (
        <span
          className="w-3 h-2 rounded-[2px] shrink-0"
          style={{ background: color, opacity: 0.85 }}
        />
      )}
      {label}
    </div>
  );
}

/** Comfortable framing for plot polygons (not India-wide, not clipped). */
const ALL_PAD = 0.55;
const ALL_MAX_ZOOM = 16;
const SELECT_PAD = 0.45;
const SELECT_MAX_ZOOM = 17;
const POINT_ZOOM = 15;

/** Read-only satellite map — one plot or many farm boundaries. */
export default function PlotBoundaryMapInner({
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
  geometry?: Geom;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
  plots?: MapPlot[];
  selectedPlotKey?: string | null;
  onSelectPlot?: (plotKey: string) => void;
  minHeight?: number;
  measuredFootprint?: MeasuredFootprint | null;
  ndvi?: NdviSeries | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const boundsByKeyRef = useRef<Map<string, L.LatLngBounds>>(new Map());
  const multiRef = useRef(false);
  const selectedKeyRef = useRef(selectedPlotKey);
  const onSelectRef = useRef(onSelectPlot);
  selectedKeyRef.current = selectedPlotKey;
  onSelectRef.current = onSelectPlot;

  useEffect(() => {
    const el = containerRef.current;
    if (!el || mapRef.current) return;

    const map = L.map(el, {
      center: [20.5937, 78.9629],
      zoom: 5,
      zoomControl: true,
    });

    // Imagery is the default: this is a remote-sensing product, and a road
    // map undersells what the assessment is actually looking at. The
    // cartographic base exists for orientation — village names, roads —
    // which imagery cannot give you.
    const imagery = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        attribution:
          'Imagery &copy; Esri · Analysis contains modified Copernicus Sentinel-2 data',
        maxZoom: 19,
      }
    ).addTo(map);

    const carto = L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      {
        attribution:
          '&copy; OpenStreetMap contributors &copy; CARTO · Analysis contains modified Copernicus Sentinel-2 data',
        maxZoom: 19,
      }
    );

    L.control
      .layers({ Imagery: imagery, 'Map (names & roads)': carto }, undefined, {
        collapsed: true,
        position: 'topright',
      })
      .addTo(map);

    // Non-negotiable in a geospatial product whose output is evidence in a
    // credit file: a reader must be able to judge the size of what they see.
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);

    mapRef.current = map;

    const t = window.setTimeout(() => safeInvalidate(mapRef.current), 50);

    return () => {
      window.clearTimeout(t);
      try {
        map.remove();
      } catch {
        /* ignore */
      }
      if (mapRef.current === map) mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  // Draw layers when plot geometries change (not on every selection)
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapAlive(map)) return;

    let cancelled = false;
    let raf = 0;

    if (layerRef.current) {
      try {
        map.removeLayer(layerRef.current);
      } catch {
        /* ignore */
      }
      layerRef.current = null;
    }

    const group = L.layerGroup().addTo(map);
    layerRef.current = group;
    boundsByKeyRef.current = new Map();

    const features: MapPlot[] =
      plots && plots.length > 0
        ? plots
        : [{ geometry, centroid, label, plot_key: 'single' }];

    const multi = features.length > 1;
    multiRef.current = multi;
    const allBounds = L.latLngBounds([]);
    let drew = false;
    const activeKey = selectedKeyRef.current;

    for (const f of features) {
      const key = String(f.plot_key || f.label || '');
      const geom = f.geometry;
      const center = f.centroid || centroidOf(geom);
      const isActive = Boolean(activeKey && key && key === activeKey);
      const style = multi
        ? isActive
          ? STYLE_ACTIVE
          : STYLE_DIM
        : STYLE_SINGLE;

      const attachClick = (layer: LayerWithKey) => {
        if (!key) return;
        layer.__plotKey = key;
        layer.on('click', (e: L.LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e);
          onSelectRef.current?.(key);
        });
      };

      if (geom?.type && geom.coordinates) {
        try {
          const layer = L.geoJSON(geom as GeoJSON.GeoJsonObject, {
            style,
          }) as L.GeoJSON & LayerWithKey;
          if (f.label) layer.bindTooltip(f.label);
          attachClick(layer);
          layer.addTo(group);
          const b = layer.getBounds();
          if (b.isValid()) {
            allBounds.extend(b);
            if (key) boundsByKeyRef.current.set(key, b);
          }
          drew = true;
          continue;
        } catch {
          /* fall through */
        }
      }
      if (center) {
        const marker = L.circleMarker([center.lat, center.lng], {
          radius: isActive ? 11 : 8,
          color: isActive ? MAP_COLORS.warn : MAP_COLORS.boundary,
          fillColor: isActive ? MAP_COLORS.warnFill : MAP_COLORS.boundary,
          fillOpacity: 0.85,
          weight: isActive ? 3 : 2,
        }) as L.CircleMarker & LayerWithKey;
        if (f.label) marker.bindTooltip(f.label);
        attachClick(marker);
        marker.addTo(group);
        const b = L.latLngBounds(
          [center.lat, center.lng],
          [center.lat, center.lng]
        );
        allBounds.extend(b);
        if (key) boundsByKeyRef.current.set(key, b);
        drew = true;
      }
    }

    // ── Measured footprint, when it is NOT the declared boundary ──────────
    //
    // The P0 case. When the supplied polygon fails QA the collector measures
    // a circular buffer around the centroid instead, so every number on the
    // page can describe land NEAR the parcel rather than the parcel. Drawing
    // only the declared polygon lets the reader believe we measured it.
    //
    // buffer_km_used gives the real radius, so this is drawn geometry, not an
    // illustration of one.
    if (measuredFootprint?.substituted && measuredFootprint.bufferKm) {
      const single = features.length === 1 ? features[0] : null;
      const c = single?.centroid || centroidOf(single?.geometry);
      if (c) {
        L.circle([c.lat, c.lng], {
          radius: measuredFootprint.bufferKm * 1000,
          ...parcelStyle('focused'),
          fillOpacity: 0.1,
        })
          .bindTooltip(
            `Measured footprint · ${measuredFootprint.bufferKm.toFixed(2)} km radius`
          )
          .addTo(group);
        // The declared boundary is restyled as "declared" — greyed and dashed —
        // so it cannot be mistaken for the footprint the score came from.
        group.eachLayer((raw) => {
          if (raw instanceof L.GeoJSON) raw.setStyle(parcelStyle('declared'));
        });
        const cb = L.circle([c.lat, c.lng], {
          radius: measuredFootprint.bufferKm * 1000,
        }).getBounds();
        if (cb.isValid()) allBounds.extend(cb);
      }
    }

    if (cancelled || !isMapAlive(map)) return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };

    if (drew && allBounds.isValid()) {
      const selBounds =
        activeKey != null ? boundsByKeyRef.current.get(activeKey) : undefined;
      if (selBounds?.isValid()) {
        safeFitBounds(map, selBounds.pad(SELECT_PAD), {
          maxZoom: SELECT_MAX_ZOOM,
          animate: false,
        });
      } else {
        safeFitBounds(map, allBounds.pad(ALL_PAD), {
          maxZoom: ALL_MAX_ZOOM,
          animate: false,
        });
      }
    } else if (features.length === 1) {
      const center = features[0].centroid || centroidOf(features[0].geometry);
      if (center && isMapAlive(map)) {
        try {
          map.setView([center.lat, center.lng], POINT_ZOOM);
        } catch {
          /* ignore */
        }
      }
    }

    raf = requestAnimationFrame(() => {
      if (cancelled || mapRef.current !== map) return;
      safeInvalidate(map);
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [geometry, centroid, label, plots, measuredFootprint]);

  // Selection: restyle + zoom (no map teardown)
  useEffect(() => {
    const map = mapRef.current;
    if (!isMapAlive(map)) return;

    const group = layerRef.current;
    if (group) applySelectionStyles(group, selectedPlotKey, multiRef.current);

    if (!selectedPlotKey) return;

    const b = boundsByKeyRef.current.get(selectedPlotKey);
    if (b?.isValid()) {
      safeFitBounds(map, b.pad(SELECT_PAD), {
        maxZoom: SELECT_MAX_ZOOM,
        animate: true,
      });
    }

    let cancelled = false;
    const raf = requestAnimationFrame(() => {
      if (cancelled || mapRef.current !== map) return;
      safeInvalidate(map);
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [selectedPlotKey]);

  // ── NDVI over time, as a parcel-mean tint ────────────────────────────────
  // Only bins that carry a value are selectable: scrubbing onto a cloud gap
  // and seeing the previous week's colour would present a stale measurement
  // as a current one.
  const observedBins = useMemo(
    () =>
      (ndvi?.dates ?? [])
        .map((d, i) => ({ i, date: String(d), value: ndvi!.values[i] }))
        .filter(
          (b): b is { i: number; date: string; value: number } =>
            typeof b.value === 'number' && Number.isFinite(b.value)
        ),
    [ndvi]
  );

  const [binPos, setBinPos] = useState<number | null>(null);
  const activeBin =
    observedBins.length > 0
      ? observedBins[Math.min(binPos ?? observedBins.length - 1, observedBins.length - 1)]
      : null;

  useEffect(() => {
    const group = layerRef.current;
    if (!group || !activeBin || multiRef.current || measuredFootprint?.substituted) return;
    const fill = vegetationAt(activeBin.value);
    group.eachLayer((raw) => {
      if (raw instanceof L.GeoJSON) {
        raw.setStyle({ ...STYLE_SINGLE, fillColor: fill, fillOpacity: 0.72 });
      }
    });
  }, [activeBin, measuredFootprint]);

  const hasAny =
    (plots && plots.some((p) => p.geometry || p.centroid)) ||
    geometry ||
    centroid;

  return (
    <div
      className="relative rounded-xl overflow-hidden border border-rule bg-paper h-full"
      style={{ minHeight }}
    >
      <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight }} />

      {/* Permanent, not hover-revealed. A map carrying marks with no key is
          an illustration, and this one is evidence in a credit file. */}
      {hasAny && (
        <div
          className="absolute z-[600] bottom-3 right-3 rounded-lg border border-rule bg-paper-raised/95 px-2.5 py-2 pointer-events-none max-w-[190px]"
          aria-hidden
        >
          {measuredFootprint?.substituted ? (
            <>
              <LegendRow color={MAP_COLORS.focus} label="Measured footprint" />
              <LegendRow color="#A8A29E" label="Declared boundary" dashed />
              <p className="text-[10px] text-ink-muted mt-1.5 leading-snug">
                The score describes the measured area, not the declared one.
              </p>
            </>
          ) : (
            <>
              <LegendRow color={MAP_COLORS.boundary} label="Assessed parcel" />
              {(plots?.length ?? 0) > 1 && (
                <LegendRow color={MAP_COLORS.warn} label="Selected" />
              )}
            </>
          )}
        </div>
      )}

      {/* Scrubber. Absent when there is nothing to scrub, or when the tint
          would be misleading — a portfolio map (many parcels, one value) or a
          substituted footprint (the polygon is not what was measured). */}
      {activeBin && !multiRef.current && !measuredFootprint?.substituted && (
        <div className="absolute z-[600] bottom-3 left-3 right-3 sm:right-auto sm:w-[280px] rounded-lg border border-rule bg-paper-raised/95 px-3 py-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-ink-muted">
              NDVI · parcel mean
            </span>
            <span className="font-mono tabular-nums text-[12px] text-ink">
              {activeBin.value.toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={observedBins.length - 1}
            value={binPos ?? observedBins.length - 1}
            onChange={(e) => setBinPos(Number(e.target.value))}
            className="w-full mt-1.5 accent-accent"
            aria-label={`Observation date: ${activeBin.date}, NDVI ${activeBin.value.toFixed(2)}`}
          />
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[10px] font-mono text-ink-muted">{activeBin.date}</span>
            <span className="text-[10px] text-ink-muted">
              {observedBins.length} observed
            </span>
          </div>
          {/* Says what it is. There is no raster behind this — one measured
              value shades the whole parcel, and a reader must not infer
              within-field variation from it. */}
          <p className="text-[10px] text-ink-muted mt-1 leading-snug">
            One measured value per date, shading the whole parcel — not per-pixel imagery.
          </p>
        </div>
      )}

      {!hasAny && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-[500]">
          <p className="text-xs text-stone-600 bg-white/90 px-3 py-1.5 rounded-lg border border-rule">
            No boundary geometry for this plot
          </p>
        </div>
      )}
    </div>
  );
}
