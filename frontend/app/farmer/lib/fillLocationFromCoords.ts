/** Reverse-geocode lat/lng into LGD-ish FarmerLocation fields via Nominatim + /api/location. */

import type { FarmerLocation, LgdSelection } from '../types';

interface LocItem {
  lgd_code: string;
  name: string;
}

function norm(s: string): string {
  return s
    .toLowerCase()
    .replace(/\b(district|state|taluk|taluka|tehsil|sub[- ]?district)\b/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function bestMatch(items: LocItem[], candidates: string[]): LocItem | null {
  const cleaned = candidates.map(norm).filter(Boolean);
  if (!cleaned.length || !items.length) return null;

  for (const c of cleaned) {
    const exact = items.find((i) => norm(i.name) === c);
    if (exact) return exact;
  }
  for (const c of cleaned) {
    const starts = items.find(
      (i) => norm(i.name).startsWith(c) || c.startsWith(norm(i.name))
    );
    if (starts) return starts;
  }
  for (const c of cleaned) {
    const partial = items.find(
      (i) => norm(i.name).includes(c) || c.includes(norm(i.name))
    );
    if (partial) return partial;
  }
  return null;
}

function toSel(item: LocItem | null): LgdSelection | null {
  return item ? { lgd_code: item.lgd_code, name: item.name } : null;
}

/**
 * Fill state / district / taluka / village from a map coordinate.
 * Preserves katha_number and existing mapCenter unless overridden.
 */
export async function fillLocationFromCoords(
  lat: number,
  lng: number,
  current: FarmerLocation
): Promise<FarmerLocation> {
  const next: FarmerLocation = {
    ...current,
    mapCenter: { lat, lng },
  };

  try {
    const rev = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=14&addressdetails=1`,
      { headers: { Accept: 'application/json' } }
    );
    if (!rev.ok) return next;
    const data = await rev.json();
    const addr = (data?.address || {}) as Record<string, string>;

    const stateCandidates = [addr.state, addr.region].filter(Boolean);
    const districtCandidates = [
      addr.county,
      addr.state_district,
      addr.city_district,
      addr.city,
      addr.town,
    ].filter(Boolean);
    const talukaCandidates = [
      addr.municipality,
      addr.suburb,
      addr.county,
      addr.city,
      addr.town,
    ].filter(Boolean);
    const villageCandidates = [
      addr.village,
      addr.hamlet,
      addr.suburb,
      addr.neighbourhood,
      addr.locality,
    ].filter(Boolean);

    const statesRes = await fetch('/api/location/states');
    const statesJson = await statesRes.json();
    const states: LocItem[] = statesJson.states || [];
    const state = bestMatch(states, stateCandidates);
    if (state) {
      next.state = toSel(state);

      const distRes = await fetch(
        `/api/location/districts?state_code=${encodeURIComponent(state.lgd_code)}`
      );
      const distJson = await distRes.json();
      const districts: LocItem[] = distJson.districts || [];
      const district = bestMatch(districts, districtCandidates);
      if (district) {
        next.district = toSel(district);

        const talRes = await fetch(
          `/api/location/talukas?district_code=${encodeURIComponent(district.lgd_code)}`
        );
        const talJson = await talRes.json();
        const talukas: LocItem[] = talJson.talukas || [];
        const taluka = bestMatch(talukas, talukaCandidates);
        if (taluka) {
          next.taluka = toSel(taluka);
        } else if (talukaCandidates[0]) {
          next.taluka = { lgd_code: '', name: String(talukaCandidates[0]) };
        }

        const vilParams = new URLSearchParams();
        if (next.taluka?.lgd_code) vilParams.set('taluka_code', next.taluka.lgd_code);
        else vilParams.set('district_code', district.lgd_code);
        if (villageCandidates[0]) vilParams.set('q', String(villageCandidates[0]));
        const vilRes = await fetch(`/api/location/villages?${vilParams}`);
        const vilJson = await vilRes.json();
        const villages: LocItem[] = vilJson.villages || [];
        const village = bestMatch(villages, villageCandidates);
        if (village) {
          next.village = toSel(village);
        } else if (villageCandidates[0]) {
          next.village = { lgd_code: '', name: String(villageCandidates[0]) };
        }
      } else if (districtCandidates[0]) {
        next.district = { lgd_code: '', name: String(districtCandidates[0]) };
      }
    } else if (stateCandidates[0]) {
      next.state = { lgd_code: '', name: String(stateCandidates[0]) };
      if (districtCandidates[0]) {
        next.district = { lgd_code: '', name: String(districtCandidates[0]) };
      }
    }
  } catch {
    /* keep mapCenter only */
  }

  return next;
}
