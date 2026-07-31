'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import '@geoman-io/leaflet-geoman-free';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';
import type { FarmPolygon } from '../types';
import { polygonAreaHa, polygonCentroid, CROP_OPTIONS, FARM_COLORS } from '../types';

interface Props {
  farms: FarmPolygon[];
  onFarmsChange: (farms: FarmPolygon[]) => void;
  mapCenter?: { lat: number; lng: number } | null;
  onBoundaryDrawn?: (centroid: { lat: number; lng: number }) => void;
}

export default function FarmBoundaryMapInner({
  farms,
  onFarmsChange,
  mapCenter,
  onBoundaryDrawn,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerGroupRef = useRef<L.FeatureGroup | null>(null);
  const [pendingRing, setPendingRing] = useState<number[][] | null>(null);
  const [draft, setDraft] = useState({
    farm_name: '',
    farm_number: '',
    primary_crop: 'Rice',
    sowing_date: '',
  });
  const [coordQuery, setCoordQuery] = useState('');
  const [coordError, setCoordError] = useState<string | null>(null);
  const [coordVertices, setCoordVertices] = useState<[number, number][]>([]);
  const searchMarkerRef = useRef<L.CircleMarker | null>(null);
  const farmsRef = useRef(farms);
  farmsRef.current = farms;
  const pendingRingRef = useRef<number[][] | null>(null);
  pendingRingRef.current = pendingRing;
  const onBoundaryDrawnRef = useRef(onBoundaryDrawn);
  onBoundaryDrawnRef.current = onBoundaryDrawn;

  const nextPlotLabel = useCallback(() => {
    return `Plot ${farmsRef.current.length + 1}`;
  }, []);

  const parseCoordPair = (raw: string): { lat: number; lng: number } | null => {
    const cleaned = raw.trim().replace(/[°]/g, '');
    const parts = cleaned.split(/[,;\s]+/).filter(Boolean);
    if (parts.length < 2) return null;
    const lat = parseFloat(parts[0]);
    const lng = parseFloat(parts[1]);
    if (Number.isNaN(lat) || Number.isNaN(lng)) return null;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
    return { lat, lng };
  };

  const redrawAll = useCallback(() => {
    const map = mapRef.current;
    const group = layerGroupRef.current;
    if (!map || !group) return;
    group.clearLayers();

    farmsRef.current.forEach((f) => {
      const layer = L.geoJSON(
        { type: 'Feature', properties: {}, geometry: f.boundary } as GeoJSON.Feature,
        {
          style: {
            color: f.color || '#22c55e',
            weight: 2,
            fillColor: f.color || '#22c55e',
            fillOpacity: 0.25,
          },
        }
      );
      layer.bindTooltip(f.farm_name || 'Farm', { sticky: true });
      group.addLayer(layer);
    });

    const pending = pendingRingRef.current;
    if (pending?.length) {
      const pendingLayer = L.geoJSON(
        {
          type: 'Feature',
          properties: {},
          geometry: { type: 'Polygon', coordinates: [pending] },
        } as GeoJSON.Feature,
        {
          style: {
            color: '#f59e0b',
            weight: 3,
            dashArray: '8 4',
            fillColor: '#fbbf24',
            fillOpacity: 0.35,
          },
        }
      );
      pendingLayer.bindTooltip('New boundary — save to confirm', { sticky: true });
      group.addLayer(pendingLayer);
      try {
        const bounds = pendingLayer.getBounds();
        if (bounds.isValid()) {
          map.fitBounds(bounds, { padding: [24, 24], maxZoom: 17 });
        }
      } catch {
        /* ignore */
      }
    }
  }, []);

  const beginPendingRing = useCallback(
    (ring: number[][]) => {
      pendingRingRef.current = ring;
      setPendingRing(ring);
      setDraft({
        farm_name: nextPlotLabel(),
        farm_number: '',
        primary_crop: 'Rice',
        sowing_date: '',
      });
      const centroid = polygonCentroid(ring);
      onBoundaryDrawnRef.current?.(centroid);
      requestAnimationFrame(() => redrawAll());
    },
    [nextPlotLabel, redrawAll]
  );

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const center: L.LatLngExpression = mapCenter
      ? [mapCenter.lat, mapCenter.lng]
      : [20.5937, 78.9629];

    const map = L.map(containerRef.current, {
      center,
      zoom: mapCenter ? 14 : 5,
      zoomControl: true,
    });

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 19,
    });

    const esri = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: 'Tiles &copy; Esri',
        maxZoom: 19,
      }
    );

    esri.addTo(map);
    L.control
      .layers({ 'Satellite (Esri)': esri, 'Street (OSM)': osm }, {}, { position: 'topleft' })
      .addTo(map);

    const group = L.featureGroup().addTo(map);
    layerGroupRef.current = group;

    map.pm.addControls({
      position: 'topright',
      drawMarker: false,
      drawCircle: false,
      drawCircleMarker: false,
      drawPolyline: false,
      drawRectangle: false,
      drawText: false,
      drawPolygon: true,
      editMode: true,
      dragMode: false,
      cutPolygon: false,
      removalMode: true,
      rotateMode: false,
    });

    map.on('pm:create', (e: { layer: L.Layer }) => {
      const layer = e.layer as L.Polygon;
      const latlngs = layer.getLatLngs()[0] as L.LatLng[];
      const ring: number[][] = latlngs.map((ll) => [ll.lng, ll.lat]);
      if (
        ring.length &&
        (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])
      ) {
        ring.push([...ring[0]]);
      }
      map.removeLayer(layer);
      beginPendingRing(ring);
    });

    mapRef.current = map;
    redrawAll();

    return () => {
      map.remove();
      mapRef.current = null;
      layerGroupRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    redrawAll();
  }, [farms, pendingRing, redrawAll]);

  useEffect(() => {
    if (mapCenter && mapRef.current) {
      mapRef.current.setView([mapCenter.lat, mapCenter.lng], 14, { animate: true });
    }
  }, [mapCenter]);

  const savePending = () => {
    if (!pendingRing) return;
    const color = FARM_COLORS[farms.length % FARM_COLORS.length];
    const farm: FarmPolygon = {
      farm_id: crypto.randomUUID(),
      farm_name: draft.farm_name.trim() || nextPlotLabel(),
      farm_number: draft.farm_number.trim(),
      boundary: { type: 'Polygon', coordinates: [pendingRing] },
      area_ha: Math.round(polygonAreaHa(pendingRing) * 1000) / 1000,
      centroid: polygonCentroid(pendingRing),
      primary_crop: draft.primary_crop,
      sowing_date: draft.sowing_date,
      color,
    };
    onFarmsChange([...farms, farm]);
    setPendingRing(null);
    pendingRingRef.current = null;
  };

  const cancelPending = () => {
    setPendingRing(null);
    pendingRingRef.current = null;
  };

  const clearAll = () => {
    onFarmsChange([]);
    setCoordVertices([]);
    cancelPending();
    setCoordError(null);
    if (searchMarkerRef.current && mapRef.current) {
      mapRef.current.removeLayer(searchMarkerRef.current);
      searchMarkerRef.current = null;
    }
  };

  const searchCoords = () => {
    const pair = parseCoordPair(coordQuery);
    if (!pair) {
      setCoordError('Enter coordinates as lat, lng — e.g. 18.49753, 73.84690');
      return;
    }
    setCoordError(null);
    const map = mapRef.current;
    if (!map) return;
    map.setView([pair.lat, pair.lng], Math.max(map.getZoom(), 16), { animate: true });
    if (searchMarkerRef.current) {
      map.removeLayer(searchMarkerRef.current);
    }
    searchMarkerRef.current = L.circleMarker([pair.lat, pair.lng], {
      radius: 7,
      color: '#2563eb',
      fillColor: '#3b82f6',
      fillOpacity: 0.85,
      weight: 2,
    })
      .bindTooltip(`${pair.lat.toFixed(6)}, ${pair.lng.toFixed(6)}`)
      .addTo(map);
  };

  const addManualVertex = () => {
    const pair = parseCoordPair(coordQuery);
    if (!pair) {
      setCoordError('Enter coordinates as lat, lng — e.g. 18.49753, 73.84690');
      return;
    }
    setCoordError(null);
    const next = [...coordVertices, [pair.lng, pair.lat] as [number, number]];
    setCoordVertices(next);
    setCoordQuery('');
    const map = mapRef.current;
    if (map) {
      map.setView([pair.lat, pair.lng], Math.max(map.getZoom(), 15));
      L.circleMarker([pair.lat, pair.lng], { radius: 4, color: '#22c55e' }).addTo(map);
    }
  };

  const closeManualPolygon = () => {
    if (coordVertices.length < 3) return;
    const ring: number[][] = coordVertices.map(([lng, lat]) => [lng, lat]);
    ring.push([...ring[0]]);
    beginPendingRing(ring);
    setCoordVertices([]);
  };

  const totalArea = farms.reduce((s, f) => s + (f.area_ha || 0), 0);

  return (
    <div className="flex flex-col h-full gap-3">
      <div className="flex flex-wrap gap-2 items-center">
        <button
          type="button"
          onClick={clearAll}
          className="text-xs px-3 py-1.5 rounded-lg border border-[#E4DFD4] text-red-600 hover:bg-red-50"
        >
          Clear All
        </button>
        <div className="flex flex-1 flex-wrap items-center gap-1.5 min-w-[220px]">
          <input
            type="text"
            inputMode="decimal"
            placeholder="18.497530040758743, 73.84689649015469"
            value={coordQuery}
            onChange={(e) => {
              setCoordQuery(e.target.value);
              if (coordError) setCoordError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                searchCoords();
              }
            }}
            className="flex-1 min-w-[200px] bg-white border border-[#E4DFD4] rounded-lg px-3 py-1.5 text-sm text-stone-800 placeholder-stone-400"
          />
          <button
            type="button"
            onClick={searchCoords}
            className="px-3 py-1.5 rounded-lg border border-emerald-600 bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-500"
          >
            Search
          </button>
          <button
            type="button"
            onClick={addManualVertex}
            className="px-3 py-1.5 rounded-lg border border-[#E4DFD4] text-sky-700 text-xs font-medium hover:bg-sky-50"
          >
            Add vertex
          </button>
          {coordVertices.length >= 3 && (
            <button
              type="button"
              onClick={closeManualPolygon}
              className="px-3 py-1.5 rounded-lg border border-emerald-500/40 text-emerald-700 text-xs hover:bg-emerald-50"
            >
              Close polygon ({coordVertices.length})
            </button>
          )}
        </div>
      </div>
      {coordError && <p className="text-xs text-red-600">{coordError}</p>}

      <div className="farm-map-container flex-1 relative" style={{ minHeight: 420 }}>
        <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 420 }} />
        <div className="farm-map-area-badge">
          {farms.length} farm(s) · {totalArea.toFixed(3)} ha
        </div>
        {farms.length > 0 && (
          <div className="farm-map-legend">
            <p className="font-semibold text-stone-500 mb-1">Farms</p>
            {farms.map((f) => (
              <div key={f.farm_id} className="farm-map-legend-item">
                <span
                  className="farm-map-legend-swatch"
                  style={{ background: f.color || '#22c55e' }}
                />
                <span className="truncate">{f.farm_name}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {pendingRing && (
        <div className="bg-white border border-emerald-500/30 rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-bold text-emerald-700">
            Name this farm ({polygonAreaHa(pendingRing).toFixed(3)} ha)
          </h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <input
              className="bg-white border border-[#E4DFD4] rounded-lg px-3 py-2 text-sm text-stone-800"
              placeholder="Farm name"
              value={draft.farm_name}
              onChange={(e) => setDraft({ ...draft, farm_name: e.target.value })}
            />
            <input
              className="bg-white border border-[#E4DFD4] rounded-lg px-3 py-2 text-sm text-stone-800"
              placeholder="Farm / Katha number"
              value={draft.farm_number}
              onChange={(e) => setDraft({ ...draft, farm_number: e.target.value })}
            />
            <select
              className="bg-white border border-[#E4DFD4] rounded-lg px-3 py-2 text-sm text-stone-800"
              value={draft.primary_crop}
              onChange={(e) => setDraft({ ...draft, primary_crop: e.target.value })}
            >
              {CROP_OPTIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <input
              type="date"
              className="bg-white border border-[#E4DFD4] rounded-lg px-3 py-2 text-sm text-stone-800"
              value={draft.sowing_date}
              onChange={(e) => setDraft({ ...draft, sowing_date: e.target.value })}
            />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={savePending}
              className="bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold px-4 py-2 rounded-lg"
            >
              Save Farm
            </button>
            <button
              type="button"
              onClick={cancelPending}
              className="text-sm text-stone-500 px-3 py-2 hover:text-stone-800"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
