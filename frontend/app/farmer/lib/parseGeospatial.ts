/** Parse uploaded geospatial files into GeoJSON polygons for farm boundaries. */

import { kml } from '@tmcw/togeojson';
import shp from 'shpjs';
import JSZip from 'jszip';
import type { FarmPolygon } from '../types';
import { FARM_COLORS, polygonAreaHa, polygonCentroid } from '../types';

type Ring = number[][];

function closeRing(ring: Ring): Ring {
  if (!ring.length) return ring;
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) {
    return [...ring, [...first]];
  }
  return ring;
}

function ringsFromGeometry(geom: GeoJSON.Geometry | null | undefined): Ring[] {
  if (!geom) return [];
  if (geom.type === 'Polygon') {
    const outer = geom.coordinates?.[0];
    return outer?.length ? [closeRing(outer as Ring)] : [];
  }
  if (geom.type === 'MultiPolygon') {
    return (geom.coordinates || [])
      .map((poly) => poly?.[0])
      .filter((r): r is Ring => !!r && r.length >= 3)
      .map(closeRing);
  }
  if (geom.type === 'GeometryCollection') {
    return (geom.geometries || []).flatMap((g) => ringsFromGeometry(g));
  }
  return [];
}

function farmsFromFeatureCollection(
  fc: GeoJSON.FeatureCollection,
  startIndex: number
): FarmPolygon[] {
  const out: FarmPolygon[] = [];
  let i = startIndex;
  for (const feature of fc.features || []) {
    const rings = ringsFromGeometry(feature.geometry);
    for (const ring of rings) {
      if (ring.length < 4) continue;
      const props = (feature.properties || {}) as Record<string, unknown>;
      const name =
        (typeof props.name === 'string' && props.name) ||
        (typeof props.Name === 'string' && props.Name) ||
        (typeof props.farm_name === 'string' && props.farm_name) ||
        `Imported plot ${i + 1}`;
      const color = FARM_COLORS[i % FARM_COLORS.length];
      out.push({
        farm_id: crypto.randomUUID(),
        farm_name: String(name),
        farm_number:
          typeof props.farm_number === 'string'
            ? props.farm_number
            : typeof props.katha === 'string'
              ? props.katha
              : '',
        boundary: { type: 'Polygon', coordinates: [ring] },
        area_ha: Math.round(polygonAreaHa(ring) * 1000) / 1000,
        centroid: polygonCentroid(ring),
        primary_crop:
          typeof props.primary_crop === 'string'
            ? props.primary_crop
            : typeof props.crop === 'string'
              ? props.crop
              : 'Rice',
        sowing_date: typeof props.sowing_date === 'string' ? props.sowing_date : '',
        color,
      });
      i += 1;
    }
  }
  return out;
}

async function parseGeoJsonText(text: string): Promise<GeoJSON.FeatureCollection> {
  const data = JSON.parse(text) as GeoJSON.GeoJSON;
  if (data.type === 'FeatureCollection') return data;
  if (data.type === 'Feature') {
    return { type: 'FeatureCollection', features: [data] };
  }
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: data as GeoJSON.Geometry,
      },
    ],
  };
}

async function parseKmlText(text: string): Promise<GeoJSON.FeatureCollection> {
  const dom = new DOMParser().parseFromString(text, 'text/xml');
  return kml(dom) as GeoJSON.FeatureCollection;
}

async function parseKmzFile(file: File): Promise<GeoJSON.FeatureCollection> {
  const zip = await JSZip.loadAsync(await file.arrayBuffer());
  const kmlEntry = Object.keys(zip.files).find((n) => n.toLowerCase().endsWith('.kml'));
  if (!kmlEntry) throw new Error('KMZ archive has no .kml file inside.');
  const text = await zip.files[kmlEntry].async('text');
  return parseKmlText(text);
}

async function parseShapefileZip(file: File): Promise<GeoJSON.FeatureCollection> {
  const buf = await file.arrayBuffer();
  const parsed = await shp(buf);
  if (Array.isArray(parsed)) {
    return {
      type: 'FeatureCollection',
      features: parsed.flatMap((fc) => fc.features || []),
    };
  }
  return parsed as GeoJSON.FeatureCollection;
}

/**
 * Convert an uploaded geospatial file into FarmPolygon[].
 * Supports: .geojson, .json, .kml, .kmz, .zip (shapefile).
 * .gpkg: ask for export to GeoJSON/KML/shapefile ZIP (browser SQLite stack is heavy).
 */
export async function parseGeospatialFile(
  file: File,
  existingCount = 0
): Promise<FarmPolygon[]> {
  const name = file.name.toLowerCase();

  let fc: GeoJSON.FeatureCollection;

  if (name.endsWith('.geojson') || name.endsWith('.json')) {
    fc = await parseGeoJsonText(await file.text());
  } else if (name.endsWith('.kml')) {
    fc = await parseKmlText(await file.text());
  } else if (name.endsWith('.kmz')) {
    fc = await parseKmzFile(file);
  } else if (name.endsWith('.zip')) {
    fc = await parseShapefileZip(file);
  } else if (name.endsWith('.shp')) {
    throw new Error(
      'Upload a .zip containing .shp + .shx + .dbf (and optional .prj), not a lone .shp file.'
    );
  } else if (name.endsWith('.gpkg')) {
    throw new Error(
      'GeoPackage (.gpkg) is not parsed in-browser yet. Export to GeoJSON, KML, or a shapefile ZIP and upload that.'
    );
  } else {
    throw new Error(
      'Unsupported format. Use GeoJSON, KML/KMZ, or a shapefile ZIP (.shp+.shx+.dbf).'
    );
  }

  const farms = farmsFromFeatureCollection(fc, existingCount);
  if (!farms.length) {
    throw new Error('No polygon boundaries found in that file.');
  }
  return farms;
}

export const GEOSPATIAL_ACCEPT =
  '.geojson,.json,.kml,.kmz,.zip,.shp,.gpkg,application/geo+json,application/json,application/vnd.google-earth.kml+xml,application/zip';
