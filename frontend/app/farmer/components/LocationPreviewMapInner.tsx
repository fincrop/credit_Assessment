'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { MAP_COLORS } from '../../lib/mapStyle';
interface Props {
  center?: { lat: number; lng: number } | null;
  label?: string;
}

/** Lightweight read-only map for Step 2 location preview */
export default function LocationPreviewMapInner({ center, label }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.CircleMarker | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: [20.5937, 78.9629],
      zoom: 5,
      zoomControl: true,
      attributionControl: true,
    });
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: 'Tiles &copy; Esri',
        maxZoom: 19,
      }
    ).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !center) return;
    map.setView([center.lat, center.lng], 11, { animate: true });
    if (markerRef.current) {
      markerRef.current.setLatLng([center.lat, center.lng]);
      if (label) markerRef.current.bindTooltip(label);
    } else {
      const marker = L.circleMarker([center.lat, center.lng], {
        radius: 8,
        color: MAP_COLORS.boundary,
        fillColor: MAP_COLORS.boundary,
        fillOpacity: 0.7,
        weight: 2,
      }).addTo(map);
      if (label) marker.bindTooltip(label);
      markerRef.current = marker;
    }
  }, [center, label]);

  return (
    <div className="farm-map-container relative" style={{ minHeight: 360 }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 360 }} />
      {!center && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-[500]">
          <p className="text-xs text-stone-600 bg-white/90 px-3 py-1.5 rounded-lg border border-rule">
            Select a location to preview on the map
          </p>
        </div>
      )}
    </div>
  );
}
