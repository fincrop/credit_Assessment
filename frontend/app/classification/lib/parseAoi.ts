/**
 * Parse an uploaded boundary file into areas of interest.
 *
 * Deliberately separate from `farmer/lib/parseGeospatial`, which turns a file
 * into FarmPolygon[] -- one farm per ring, each carrying a crop and a sowing
 * date. Here the file is a *village or block outline*, the crop is what we are
 * about to go and find, and a multi-part boundary is one area rather than
 * several farms. Sharing the parser would mean either polluting FarmPolygon
 * with nullable crop fields or silently splitting a village into "plots".
 */

import { kml } from '@tmcw/togeojson';
import shp from 'shpjs';
import JSZip from 'jszip';
import type { AreaOfInterest } from '../types';
import { geometryAreaHa, geometryCentroid } from '../types';

const AOI_COLORS = ['#2A78D6', '#7C5FA8', '#2E7D4F', '#B4842A', '#B93A28'];

function closeRing(ring: number[][]): number[][] {
  if (!ring.length) return ring;
  const [a, b] = [ring[0], ring[ring.length - 1]];
  return a[0] === b[0] && a[1] === b[1] ? ring : [...ring, [...a]];
}

/** Keep polygons whole, including holes and multi-parts. */
function geometriesFrom(
  geom: GeoJSON.Geometry | null | undefined
): (GeoJSON.Polygon | GeoJSON.MultiPolygon)[] {
  if (!geom) return [];
  if (geom.type === 'Polygon') {
    const rings = (geom.coordinates || []).map(closeRing).filter((r) => r.length >= 4);
    return rings.length ? [{ type: 'Polygon', coordinates: rings }] : [];
  }
  if (geom.type === 'MultiPolygon') {
    const polys = (geom.coordinates || [])
      .map((poly) => poly.map(closeRing).filter((r) => r.length >= 4))
      .filter((p) => p.length);
    return polys.length ? [{ type: 'MultiPolygon', coordinates: polys }] : [];
  }
  if (geom.type === 'GeometryCollection') {
    return (geom.geometries || []).flatMap(geometriesFrom);
  }
  return [];
}

async function parseGeoJsonText(text: string): Promise<GeoJSON.FeatureCollection> {
  const data = JSON.parse(text) as GeoJSON.GeoJSON;
  if (data.type === 'FeatureCollection') return data;
  if (data.type === 'Feature') return { type: 'FeatureCollection', features: [data] };
  return {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', properties: {}, geometry: data as GeoJSON.Geometry }],
  };
}

async function parseKmlText(text: string): Promise<GeoJSON.FeatureCollection> {
  return kml(new DOMParser().parseFromString(text, 'text/xml')) as GeoJSON.FeatureCollection;
}

async function parseKmz(file: File): Promise<GeoJSON.FeatureCollection> {
  const zip = await JSZip.loadAsync(await file.arrayBuffer());
  const entry = Object.keys(zip.files).find((n) => n.toLowerCase().endsWith('.kml'));
  if (!entry) throw new Error('KMZ archive has no .kml inside.');
  return parseKmlText(await zip.files[entry].async('text'));
}

async function parseShapefileZip(file: File): Promise<GeoJSON.FeatureCollection> {
  const parsed = await shp(await file.arrayBuffer());
  if (Array.isArray(parsed)) {
    return { type: 'FeatureCollection', features: parsed.flatMap((fc) => fc.features || []) };
  }
  return parsed as GeoJSON.FeatureCollection;
}

/**
 * How many separate areas a file becomes.
 *
 * `merge` treats every feature as one area -- the right reading for a village
 * split into survey-number polygons, where classifying them separately would
 * duplicate satellite work over the same tiles and fragment the statistics.
 * `separate` keeps them apart, for a file holding several distinct villages.
 */
export type AoiSplitMode = 'merge' | 'separate';

export async function parseAoiFile(
  file: File,
  opts: { mode?: AoiSplitMode; existingCount?: number } = {}
): Promise<AreaOfInterest[]> {
  const { mode = 'merge', existingCount = 0 } = opts;
  const name = file.name.toLowerCase();

  let fc: GeoJSON.FeatureCollection;
  if (name.endsWith('.geojson') || name.endsWith('.json')) {
    fc = await parseGeoJsonText(await file.text());
  } else if (name.endsWith('.kml')) {
    fc = await parseKmlText(await file.text());
  } else if (name.endsWith('.kmz')) {
    fc = await parseKmz(file);
  } else if (name.endsWith('.zip')) {
    fc = await parseShapefileZip(file);
  } else if (name.endsWith('.shp')) {
    throw new Error('Upload a .zip containing .shp + .shx + .dbf, not a lone .shp.');
  } else if (name.endsWith('.gpkg')) {
    throw new Error(
      'GeoPackage (.gpkg) is not read in-browser. Export to GeoJSON, KML, or a shapefile ZIP.'
    );
  } else {
    throw new Error('Unsupported format. Use GeoJSON, KML/KMZ, or a shapefile ZIP.');
  }

  const geoms = (fc.features || []).flatMap((f) => geometriesFrom(f.geometry));
  if (!geoms.length) throw new Error('No polygon boundary found in that file.');

  const base = file.name.replace(/\.[^.]+$/, '');

  if (mode === 'merge' && geoms.length > 1) {
    // Concatenate into one MultiPolygon rather than unioning: a real union
    // needs a topology library we do not ship, and the classifier only uses
    // this geometry as an extraction mask, where overlapping parts are
    // harmless. Area is computed from the parts, so an overlap would
    // double-count -- acceptable for outline parts of one village, which do
    // not overlap in practice, and visible to the user as the displayed area.
    const coordinates = geoms.flatMap((g) =>
      g.type === 'Polygon' ? [g.coordinates] : g.coordinates
    );
    const merged: GeoJSON.MultiPolygon = { type: 'MultiPolygon', coordinates };
    return [
      {
        aoi_id: crypto.randomUUID(),
        name: base,
        source: 'uploaded',
        boundary: merged,
        area_ha: Math.round(geometryAreaHa(merged) * 100) / 100,
        centroid: geometryCentroid(merged),
        color: AOI_COLORS[existingCount % AOI_COLORS.length],
      },
    ];
  }

  return geoms.map((geom, i) => {
    const props = (fc.features?.[i]?.properties || {}) as Record<string, unknown>;
    const label =
      (typeof props.name === 'string' && props.name) ||
      (typeof props.Name === 'string' && props.Name) ||
      (typeof props.village === 'string' && props.village) ||
      (geoms.length > 1 ? `${base} (${i + 1})` : base);
    return {
      aoi_id: crypto.randomUUID(),
      name: String(label),
      source: 'uploaded' as const,
      boundary: geom,
      area_ha: Math.round(geometryAreaHa(geom) * 100) / 100,
      centroid: geometryCentroid(geom),
      color: AOI_COLORS[(existingCount + i) % AOI_COLORS.length],
    };
  });
}

export const AOI_ACCEPT =
  '.geojson,.json,.kml,.kmz,.zip,application/geo+json,application/json,application/vnd.google-earth.kml+xml,application/zip';

export { AOI_COLORS };
