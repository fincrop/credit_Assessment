'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

type Geom = { type?: string; coordinates?: unknown } | null | undefined;

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

/** Read-only satellite map with farm boundary highlight. */
export default function PlotBoundaryMapInner({
  geometry,
  centroid,
  label,
}: {
  geometry?: Geom;
  centroid?: { lat: number; lng: number } | null;
  label?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | L.CircleMarker | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: [20.5937, 78.9629],
      zoom: 5,
      zoomControl: true,
    });
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: 'Tiles &copy; Esri', maxZoom: 19 }
    ).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
      layerRef.current = null;
    }

    const center = centroid || centroidOf(geometry);
    if (geometry?.type && geometry.coordinates) {
      try {
        const layer = L.geoJSON(geometry as GeoJSON.GeoJsonObject, {
          style: {
            color: '#22c55e',
            weight: 2,
            fillColor: '#22c55e',
            fillOpacity: 0.25,
          },
        }).addTo(map);
        if (label) layer.bindTooltip(label);
        layerRef.current = layer;
        const bounds = layer.getBounds();
        if (bounds.isValid()) map.fitBounds(bounds.pad(0.35));
        return;
      } catch {
        /* fall through to marker */
      }
    }

    if (center) {
      map.setView([center.lat, center.lng], 15);
      const marker = L.circleMarker([center.lat, center.lng], {
        radius: 8,
        color: '#22c55e',
        fillColor: '#22c55e',
        fillOpacity: 0.7,
        weight: 2,
      }).addTo(map);
      if (label) marker.bindTooltip(label);
      layerRef.current = marker;
    }
  }, [geometry, centroid, label]);

  return (
    <div className="relative rounded-xl overflow-hidden border border-[#E4DFD4] bg-[#F5F2EB]" style={{ minHeight: 320 }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 320 }} />
      {!geometry && !centroid && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-[500]">
          <p className="text-xs text-stone-600 bg-white/90 px-3 py-1.5 rounded-lg border border-[#E4DFD4]">
            No boundary geometry for this plot
          </p>
        </div>
      )}
    </div>
  );
}
