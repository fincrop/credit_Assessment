'use client';

import { useCallback, useEffect, useRef, type ReactNode } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import '@geoman-io/leaflet-geoman-free';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';
import type { AreaOfInterest, ClassStat } from '../types';
import { geometryAreaHa, geometryCentroid, formatHa, NON_CROP_COLORS, classColor } from '../types';
import { AOI_COLORS } from '../lib/parseAoi';
import { MAP_COLORS } from '../../lib/mapStyle';

interface Props {
  areas: AreaOfInterest[];
  onAreasChange: (areas: AreaOfInterest[]) => void;
  /** Result overlay — drawn read-only beneath the AOI outlines. */
  resultLayer?: GeoJSON.FeatureCollection | null;
  /** Called with (feature) when a classified field is clicked. */
  onFieldClick?: (props: Record<string, unknown>) => void;
  /** Hide draw controls once the job is running. */
  readOnly?: boolean;
  heightClass?: string;
  /** Crop legend (colour + area) drawn over the map. */
  legend?: ClassStat[] | null;
  /** Overlay in the map's top-right, e.g. the download menu. */
  toolbar?: ReactNode;
  /**
   * Pre-rendered raster product (PNG already in Web Mercator) drawn in its own
   * pane below the field outlines. While one is shown, fields draw as outlines.
   */
  rasterOverlay?: RasterOverlaySpec | null;
  /** Panel in the map's bottom-right, e.g. the raster product picker and legend. */
  overlay?: ReactNode;
  /** Field drawn with a heavier outline (matched on `properties.field_id`). */
  selectedFieldId?: string | null;
}

export interface RasterOverlaySpec {
  url: string;
  /** [[south, west], [north, east]] in lat/lon. */
  bounds: [[number, number], [number, number]];
  opacity: number;
}

const ESRI_IMAGERY =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const ESRI_LABELS =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
const OSM_STREETS = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';

function isNamedCrop(crop: string): boolean {
  return !(crop in NON_CROP_COLORS);
}

export default function AoiMapInner({
  areas,
  onAreasChange,
  resultLayer,
  onFieldClick,
  readOnly = false,
  heightClass = 'h-[520px]',
  legend = null,
  toolbar,
  rasterOverlay = null,
  overlay,
  selectedFieldId = null,
}: Props) {
  const shellRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const aoiGroupRef = useRef<L.FeatureGroup | null>(null);
  const resultGroupRef = useRef<L.FeatureGroup | null>(null);

  // Refs mirror props so the Leaflet callbacks registered once on mount always
  // read current values without re-registering (which would drop the map).
  const areasRef = useRef(areas);
  areasRef.current = areas;
  const onAreasChangeRef = useRef(onAreasChange);
  onAreasChangeRef.current = onAreasChange;
  const onFieldClickRef = useRef(onFieldClick);
  onFieldClickRef.current = onFieldClick;
  const resultLayerRef = useRef(resultLayer);
  resultLayerRef.current = resultLayer;
  const selectedFieldIdRef = useRef(selectedFieldId);
  selectedFieldIdRef.current = selectedFieldId;
  const outlineOnlyRef = useRef(Boolean(rasterOverlay));
  outlineOnlyRef.current = Boolean(rasterOverlay);
  const resultGeoJsonRef = useRef<L.GeoJSON | null>(null);
  const rasterLayerRef = useRef<L.ImageOverlay | null>(null);

  const redrawAoi = useCallback(() => {
    const group = aoiGroupRef.current;
    const map = mapRef.current;
    if (!group || !map) return;
    group.clearLayers();

    const hasResult = Boolean(resultLayerRef.current?.features?.length);
    areasRef.current.forEach((a) => {
      const layer = L.geoJSON(
        { type: 'Feature', properties: {}, geometry: a.boundary } as GeoJSON.Feature,
        {
          pane: 'aoiOutline',
          interactive: !hasResult,
          style: {
            color: a.color || MAP_COLORS.draw,
            weight: 2.5,
            fillColor: a.color || MAP_COLORS.draw,
            fill: !hasResult,
            fillOpacity: hasResult ? 0 : 0.12,
            dashArray: '6 4',
          },
        }
      );
      if (!hasResult) {
        layer.bindTooltip(`${a.name} · ${formatHa(a.area_ha)}`, { sticky: true });
      }
      group.addLayer(layer);
    });

    if (areasRef.current.length) {
      const b = group.getBounds();
      if (b.isValid()) {
        map.fitBounds(b, { padding: [40, 40], maxZoom: hasResult ? 17 : 15 });
      }
    }
  }, []);

  const redrawResult = useCallback(() => {
    const group = resultGroupRef.current;
    if (!group) return;
    group.clearLayers();
    resultGeoJsonRef.current = null;
    const fc = resultLayerRef.current;
    if (!fc?.features?.length) return;

    const layer = L.geoJSON(fc, {
      pane: 'fields',
      style: (feature) => {
        const crop = String(feature?.properties?.crop ?? 'Unclassified');
        const color = (feature?.properties?.color as string) || '#B0A89C';
        const named = isNamedCrop(crop);
        const fid = feature?.properties?.field_id;
        const selected =
          selectedFieldIdRef.current != null && fid != null && String(fid) === selectedFieldIdRef.current;
        // Over a raster product the field is an outline only, so the pixels
        // behind its numbers stay visible. Fill opacity 0 keeps it clickable.
        const outlineOnly = outlineOnlyRef.current;
        return {
          color: selected ? '#facc15' : outlineOnly ? '#fafaf9' : '#1c1917',
          weight: selected ? 3.2 : named ? 1.7 : 1.2,
          opacity: selected ? 1 : 0.9,
          fillColor: color,
          fillOpacity: outlineOnly ? 0 : named ? 0.58 : 0.36,
          lineJoin: 'miter' as const,
          // Field edges are already straightened and shared server-side
          // (regularize_partition). Leaflet's default per-polygon screen-space
          // simplification (smoothFactor 1) would move each copy of a shared
          // edge differently and reopen hairline gaps between neighbours.
          smoothFactor: 0,
        };
      },
      onEachFeature: (feature, lyr) => {
        const p = (feature.properties || {}) as Record<string, unknown>;
        const crop = String(p.crop ?? 'Unclassified');
        const modelCrop =
          typeof p.model_top_crop === 'string' && p.model_top_crop && p.model_top_crop !== crop
            ? ` (${p.model_top_crop})`
            : '';
        const ha = typeof p.area_ha === 'number' ? p.area_ha.toFixed(2) : '—';
        // Classification fields carry a probability; monitoring fields carry
        // status and stress instead.
        const conf =
          typeof p.confidence === 'number'
            ? `${(p.confidence * 100).toFixed(0)}%`
            : [p.status, p.stress].filter((v) => typeof v === 'string' && v).join(' · ') || '—';
        const note = typeof p.note === 'string' && p.note ? ` · ${p.note}` : '';
        lyr.bindTooltip(`${crop}${modelCrop} · ${ha} ha · ${conf}${note}`, { sticky: true, pane: 'tooltipPane' });
        lyr.on('click', () => onFieldClickRef.current?.(p));
      },
    });
    group.addLayer(layer);
    resultGeoJsonRef.current = layer;
  }, []);

  const restyleResult = useCallback(() => {
    const layer = resultGeoJsonRef.current;
    if (!layer) return;
    layer.eachLayer((lyr) => {
      layer.resetStyle(lyr);
      const fid = (lyr as L.Layer & { feature?: GeoJSON.Feature }).feature?.properties?.field_id;
      if (selectedFieldIdRef.current != null && fid != null && String(fid) === selectedFieldIdRef.current) {
        (lyr as L.Path).bringToFront?.();
      }
    });
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [20.5937, 78.9629],
      zoom: 5,
      zoomControl: true,
    });

    map.createPane('labels');
    const labelsPane = map.getPane('labels');
    if (labelsPane) {
      labelsPane.style.zIndex = '450';
      labelsPane.style.pointerEvents = 'none';
    }
    // Raster products sit above the basemap and below the field outlines.
    map.createPane('raster');
    const rasterPane = map.getPane('raster');
    if (rasterPane) {
      rasterPane.style.zIndex = '410';
      rasterPane.style.pointerEvents = 'none';
    }
    map.createPane('fields');
    const fieldsPane = map.getPane('fields');
    if (fieldsPane) fieldsPane.style.zIndex = '420';
    map.createPane('aoiOutline');
    const aoiPane = map.getPane('aoiOutline');
    if (aoiPane) {
      aoiPane.style.zIndex = '430';
      aoiPane.style.pointerEvents = 'none';
    }

    const esri = L.tileLayer(ESRI_IMAGERY, {
      attribution: 'Tiles &copy; Esri',
      maxZoom: 19,
    });
    const osm = L.tileLayer(OSM_STREETS, {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 19,
    });
    const labels = L.tileLayer(ESRI_LABELS, {
      attribution: 'Labels &copy; Esri',
      maxZoom: 19,
      pane: 'labels',
    });

    esri.addTo(map);
    labels.addTo(map);
    L.control
      .layers(
        { 'Satellite (Esri)': esri, 'Street (OSM)': osm },
        { 'Place names': labels },
        { position: 'topleft' }
      )
      .addTo(map);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);

    resultGroupRef.current = L.featureGroup([], { pane: 'fields' }).addTo(map);
    aoiGroupRef.current = L.featureGroup([], { pane: 'aoiOutline' }).addTo(map);

    map.on('pm:create', (e: { layer: L.Layer }) => {
      const poly = e.layer as L.Polygon;
      const latlngs = poly.getLatLngs()[0] as L.LatLng[];
      const ring: number[][] = latlngs.map((ll) => [ll.lng, ll.lat]);
      if (
        ring.length &&
        (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])
      ) {
        ring.push([...ring[0]]);
      }
      map.removeLayer(poly);

      const geometry: GeoJSON.Polygon = { type: 'Polygon', coordinates: [ring] };
      const current = areasRef.current;
      const next: AreaOfInterest = {
        aoi_id: crypto.randomUUID(),
        name: `Drawn area ${current.filter((a) => a.source === 'drawn').length + 1}`,
        source: 'drawn',
        boundary: geometry,
        area_ha: Math.round(geometryAreaHa(geometry) * 100) / 100,
        centroid: geometryCentroid(geometry),
        color: AOI_COLORS[current.length % AOI_COLORS.length],
      };
      onAreasChangeRef.current([...current, next]);
    });

    mapRef.current = map;
    redrawAoi();
    redrawResult();
    window.setTimeout(() => map.invalidateSize({ animate: false }), 50);

    return () => {
      map.remove();
      mapRef.current = null;
      aoiGroupRef.current = null;
      resultGroupRef.current = null;
      resultGeoJsonRef.current = null;
      rasterLayerRef.current = null;
    };
    // Mount-only: handlers read through refs, so this must not re-run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Draw controls follow readOnly without tearing the map down.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (readOnly) {
      map.pm.removeControls();
      return;
    }
    map.pm.addControls({
      position: 'topright',
      drawMarker: false,
      drawCircle: false,
      drawCircleMarker: false,
      drawPolyline: false,
      drawText: false,
      drawRectangle: true,
      drawPolygon: true,
      editMode: true,
      dragMode: false,
      cutPolygon: false,
      removalMode: false,
      rotateMode: false,
    });
  }, [readOnly]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const ro = new ResizeObserver(() => {
      mapRef.current?.invalidateSize({ animate: false });
    });
    ro.observe(shell);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    redrawAoi();
  }, [areas, redrawAoi]);

  useEffect(() => {
    redrawResult();
    redrawAoi();
    const map = mapRef.current;
    if (map) window.setTimeout(() => map.invalidateSize({ animate: false }), 50);
  }, [resultLayer, redrawResult, redrawAoi]);

  const rasterUrl = rasterOverlay?.url ?? null;
  const rasterBoundsKey = rasterOverlay ? JSON.stringify(rasterOverlay.bounds) : null;
  const rasterOpacity = rasterOverlay?.opacity ?? 1;
  const rasterOpacityRef = useRef(rasterOpacity);
  rasterOpacityRef.current = rasterOpacity;

  // The PNG is already reprojected to Web Mercator, so an image overlay on its
  // lat/lon bounds lands on the right pixels. Never re-stretched here.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (rasterLayerRef.current) {
      map.removeLayer(rasterLayerRef.current);
      rasterLayerRef.current = null;
    }
    if (!rasterUrl || !rasterBoundsKey) return;
    const bounds = JSON.parse(rasterBoundsKey) as [[number, number], [number, number]];
    rasterLayerRef.current = L.imageOverlay(rasterUrl, bounds, {
      pane: 'raster',
      opacity: rasterOpacityRef.current,
      interactive: false,
      className: 'raster-overlay',
    }).addTo(map);
  }, [rasterUrl, rasterBoundsKey]);

  useEffect(() => {
    rasterLayerRef.current?.setOpacity(rasterOpacity);
  }, [rasterOpacity]);

  const hasRaster = Boolean(rasterOverlay);
  useEffect(() => {
    restyleResult();
  }, [selectedFieldId, hasRaster, restyleResult]);

  return (
    <div ref={shellRef} className={`classification-map ${heightClass}`}>
      <div ref={containerRef} className="h-full w-full" />
      {toolbar ? (
        <div className="absolute top-3 right-3 z-[1100] pointer-events-auto">{toolbar}</div>
      ) : null}
      {overlay ? (
        <div className="absolute bottom-7 right-3 z-[1000] pointer-events-auto">{overlay}</div>
      ) : null}
      {legend && legend.length > 0 ? (
        <div className="classification-legend">
          <p className="classification-legend-title">Legend</p>
          {legend
            .filter((s) => s.area_ha > 0)
            .map((s) => (
              <div key={s.crop} className="classification-legend-row">
                <span
                  className="classification-legend-swatch"
                  style={{ background: classColor(s.crop) }}
                  aria-hidden
                />
                <span className="truncate">{s.crop}</span>
                <span className="tabular-nums text-stone-500">{formatHa(s.area_ha)}</span>
              </div>
            ))}
        </div>
      ) : null}
    </div>
  );
}
