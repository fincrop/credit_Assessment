/**
 * Spatial clustering for Agristack land_data: rejects outlier polygons (wrong coordinates)
 * that share administrative codes but lie far apart. Merges in-cluster geometries and
 * sums owner_extent for registered hectare totals.
 */

export type ParcelIngestStat = {
  parcels_total: number;
  parcels_clustered: number;
  parcels_excluded: number;
  owner_extent_sum_ha: number;
};

const EARTH_RADIUS_KM = 6371;

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (x: number) => (x * Math.PI) / 180;
  const dLat = r(lat2 - lat1);
  const dLon = r(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return EARTH_RADIUS_KM * c;
}

/** Single ring centroid (lon, lat). */
function ringCentroid(ring: number[][]): { lon: number; lat: number } | null {
  if (!ring?.length) return null;
  let sx = 0;
  let sy = 0;
  for (const pt of ring) {
    sx += Number(pt[0]);
    sy += Number(pt[1]);
  }
  const n = ring.length;
  return { lon: sx / n, lat: sy / n };
}

export function centroidFromPlotGeometry(
  geom: { type?: string; coordinates?: unknown } | null
): { lon: number; lat: number } | null {
  if (!geom?.coordinates || !geom.type) return null;
  try {
    if (geom.type === 'Point' && Array.isArray(geom.coordinates)) {
      const [lon, lat] = geom.coordinates as number[];
      if (Number.isFinite(lon) && Number.isFinite(lat)) return { lon, lat };
      return null;
    }
    if (geom.type === 'Polygon') {
      const coords = geom.coordinates as number[][][];
      const outer = coords?.[0];
      return ringCentroid(outer || []);
    }
    if (geom.type === 'MultiPolygon') {
      const multip = geom.coordinates as number[][][][];
      const first = multip?.[0]?.[0];
      return ringCentroid(first || []);
    }
  } catch {
    return null;
  }
  return null;
}

export function parseOwnerExtentHa(p: {
  owner_extent?: string | number | null;
  area_unit?: string | null;
}): number {
  if (p.owner_extent == null || p.owner_extent === '') return 0;
  const ext = typeof p.owner_extent === 'number' ? p.owner_extent : Number(String(p.owner_extent));
  if (!Number.isFinite(ext) || ext < 0) return 0;
  if (p.area_unit && String(p.area_unit).toLowerCase().includes('acre')) {
    return ext * 0.404686;
  }
  return ext;
}

type ParcelNode = {
  parcel: Record<string, unknown>;
  centroid: { lon: number; lat: number };
  extentHa: number;
};

function maxPairwiseSpreadKm(component: ParcelNode[]): number {
  if (component.length <= 1) return 0.5;
  let maxKm = 0;
  for (let i = 0; i < component.length; i++) {
    for (let j = i + 1; j < component.length; j++) {
      const a = component[i].centroid;
      const b = component[j].centroid;
      maxKm = Math.max(maxKm, haversineKm(a.lat, a.lon, b.lat, b.lon));
    }
  }
  return Math.max(maxKm, 0.25);
}

function farmClusterQualityScore(component: ParcelNode[]): number {
  const sumExt = component.reduce((s, x) => s + x.extentHa, 0);
  const spread = maxPairwiseSpreadKm(component);
  // Prefer tight clusters with real extent (rejects a far singleton with huge bogus extent)
  const density = component.length / spread;
  return (sumExt + 0.01) * Math.sqrt(density);
}

/**
 * Connected components where centroid distance ≤ maxEdgeKm, then pick the best component
 * by registered extent and geographic tightness (not raw parcel count).
 */
export function selectBestSpatialCluster<T extends Record<string, unknown>>(
  landParcels: T[],
  maxEdgeKm = 120
): T[] {
  const nodes: ParcelNode[] = [];
  for (const parcel of landParcels) {
    const geom = parcel.plot_geometry ?? parcel.farm_geometry;
    const c =
      centroidFromPlotGeometry(
        geom as { type?: string; coordinates?: unknown } | null
      );
    if (!c) continue;
    nodes.push({
      parcel,
      centroid: c,
      extentHa: parseOwnerExtentHa(parcel as { owner_extent?: string; area_unit?: string }),
    });
  }

  if (nodes.length === 0) return [];
  const n = nodes.length;
  const adj: number[][] = Array.from({ length: n }, () => []);

  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const a = nodes[i].centroid;
      const b = nodes[j].centroid;
      if (haversineKm(a.lat, a.lon, b.lat, b.lon) <= maxEdgeKm) {
        adj[i].push(j);
        adj[j].push(i);
      }
    }
  }

  const components: ParcelNode[][] = [];
  const visited = new Array<boolean>(n).fill(false);

  for (let s = 0; s < n; s++) {
    if (visited[s]) continue;
    const stack = [s];
    visited[s] = true;
    const compIdx: number[] = [];
    while (stack.length) {
      const u = stack.pop()!;
      compIdx.push(u);
      for (const v of adj[u]) {
        if (!visited[v]) {
          visited[v] = true;
          stack.push(v);
        }
      }
    }
    components.push(compIdx.map((i) => nodes[i]));
  }

  let best: ParcelNode[] = [];
  let bestScore = -1;
  for (const comp of components) {
    const sc = farmClusterQualityScore(comp);
    if (sc > bestScore) {
      bestScore = sc;
      best = comp;
    }
  }

  return best.map((x) => x.parcel as T);
}

/** GeoJSON MultiPolygon from multiple Polygon / MultiPolygon parcels. */
export function mergePlotGeometries(
  parcels: Record<string, unknown>[]
): { type: 'MultiPolygon'; coordinates: number[][][][] } | null {
  const coordinates: number[][][][] = [];
  for (const p of parcels) {
    const geom = p.plot_geometry ?? p.farm_geometry;
    if (!geom || typeof geom !== 'object') continue;
    const g = geom as { type?: string; coordinates?: unknown };
    if (g.type === 'Polygon' && Array.isArray(g.coordinates)) {
      coordinates.push(g.coordinates as number[][][]);
    } else if (g.type === 'MultiPolygon' && Array.isArray(g.coordinates)) {
      for (const poly of g.coordinates as number[][][][]) {
        coordinates.push(poly);
      }
    }
  }
  if (coordinates.length === 0) return null;
  return { type: 'MultiPolygon', coordinates };
}

export function weightedCentroidFromParcels(parcels: Record<string, unknown>[]): {
  latitude: number;
  longitude: number;
} | null {
  let wSum = 0;
  let latSum = 0;
  let lonSum = 0;
  for (const p of parcels) {
    const c = centroidFromPlotGeometry(
      (p.plot_geometry ?? p.farm_geometry) as { type?: string; coordinates?: unknown } | null
    );
    if (!c) continue;
    const w = Math.max(parseOwnerExtentHa(p as { owner_extent?: string; area_unit?: string }), 0.01);
    wSum += w;
    latSum += c.lat * w;
    lonSum += c.lon * w;
  }
  if (wSum <= 0) return null;
  return { latitude: latSum / wSum, longitude: lonSum / wSum };
}

export function buildClusteredFarmFields(
  landParcels: Record<string, unknown>[],
  maxEdgeKm?: number
): {
  clusteredParcels: Record<string, unknown>[];
  field_area_ha: number | null;
  geometry: { type: 'MultiPolygon'; coordinates: number[][][][] } | null;
  centroid: { latitude: number; longitude: number } | null;
  ingest_stats: ParcelIngestStat;
} {
  const total = landParcels.length;
  const clusteredParcels =
    total === 1
      ? landParcels
      : selectBestSpatialCluster(landParcels, maxEdgeKm ?? 120);

  const fallback = clusteredParcels.length === 0 && total > 0 ? [landParcels[0]] : clusteredParcels;

  let sumHa = 0;
  for (const p of fallback) {
    sumHa += parseOwnerExtentHa(p as { owner_extent?: string; area_unit?: string });
  }

  const geometry = mergePlotGeometries(fallback);
  const centroid = weightedCentroidFromParcels(fallback);

  const used = fallback.length;

  return {
    clusteredParcels: fallback,
    field_area_ha: sumHa > 0 ? sumHa : null,
    geometry,
    centroid,
    ingest_stats: {
      parcels_total: total,
      parcels_clustered: used,
      parcels_excluded: Math.max(0, total - used),
      owner_extent_sum_ha: sumHa > 0 ? sumHa : 0,
    },
  };
}
