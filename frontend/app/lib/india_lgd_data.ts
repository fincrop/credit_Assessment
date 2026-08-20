/**
 * India LGD helpers — backed by compiled official LGD dump
 * (states + districts + talukas in app/data/india_lgd.json).
 * Villages live in MongoDB `lgd_villages` after running the import script.
 */

import lgdData from '../data/india_lgd.json';

export interface LGDState {
  lgd_code: string;
  name: string;
  name_local?: string;
}

export interface LGDDistrict {
  lgd_code: string;
  name: string;
  state_lgd_code: string;
}

export interface LGDTaluka {
  lgd_code: string;
  name: string;
  district_lgd_code: string;
  state_lgd_code: string;
}

type LgdJson = {
  states: LGDState[];
  districts: Record<string, LGDDistrict[]>;
  talukas: Record<string, LGDTaluka[]>;
};

const data = lgdData as LgdJson;

export const INDIA_STATES: LGDState[] = data.states;

export const INDIA_DISTRICTS: LGDDistrict[] = Object.values(data.districts).flat();

export function getDistricts(stateLgdCode: string): LGDDistrict[] {
  return data.districts[stateLgdCode] ?? [];
}

export function getTalukas(districtLgdCode: string): LGDTaluka[] {
  return data.talukas[districtLgdCode] ?? [];
}

export function getStateName(lgdCode: string): string {
  return INDIA_STATES.find((s) => s.lgd_code === lgdCode)?.name ?? lgdCode;
}

export function getDistrictName(lgdCode: string): string {
  return INDIA_DISTRICTS.find((d) => d.lgd_code === lgdCode)?.name ?? lgdCode;
}

/** Name only — null when the code is missing or unknown. */
export function lookupStateName(lgdCode: string | null | undefined): string | null {
  if (lgdCode == null || lgdCode === '') return null;
  const raw = String(lgdCode).trim();
  const alt = String(Number(raw));
  return (
    INDIA_STATES.find((s) => s.lgd_code === raw || (alt !== 'NaN' && s.lgd_code === alt))
      ?.name ?? null
  );
}

export function lookupDistrictName(lgdCode: string | null | undefined): string | null {
  if (lgdCode == null || lgdCode === '') return null;
  const raw = String(lgdCode).trim();
  const alt = String(Number(raw));
  return (
    INDIA_DISTRICTS.find((d) => d.lgd_code === raw || (alt !== 'NaN' && d.lgd_code === alt))
      ?.name ?? null
  );
}
