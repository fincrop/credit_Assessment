import type { TriState } from '../types/assessment';

/** Normalize stored/API values to tri-state without coercing unknown to false. */
export function asTriState(v: unknown): TriState {
  if (v === true || v === false) return v;
  if (v === 'true' || v === 'yes') return true;
  if (v === 'false' || v === 'no') return false;
  return null;
}
