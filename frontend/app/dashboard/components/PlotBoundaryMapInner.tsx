'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { MAP_COLORS } from '../../lib/mapStyle';
type Geom = { type?: string; coordinates?: unknown } | null | undefined;

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
}: {
  geometry?: Geom;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
  plots?: MapPlot[];
  selectedPlotKey?: string | null;
  onSelectPlot?: (plotKey: string) => void;
  minHeight?: number;
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
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: 'Tiles &copy; Esri', maxZoom: 19 }
    ).addTo(map);
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
  }, [geometry, centroid, label, plots]);

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
