/**
 * Krishi Bhoomi Score (KBS) presentation helpers.
 * Internal pipeline scores stay 0–100; headline display uses 300–900.
 */

export const KBS_MIN = 300;
export const KBS_MAX = 900;

export type KbsBandId = 'poor' | 'fair' | 'good' | 'excellent';

export type KbsBand = {
  id: KbsBandId;
  name: string;
  min: number;
  max: number;
  /** Arc fill */
  color: string;
  /** Mapped overall-risk label for the pill */
  riskLabel: 'High' | 'Moderate' | 'Low' | 'Very low';
  shortDescription: string;
};

/** Four equal bands of 150 points / 45° on the 180° gauge. */
export const KBS_BANDS: KbsBand[] = [
  {
    id: 'poor',
    name: 'Poor',
    min: 300,
    max: 450,
    color: '#E24B4A',
    riskLabel: 'High',
    shortDescription: 'Land and crop condition is weak across key field-health signals.',
  },
  {
    id: 'fair',
    name: 'Fair',
    min: 450,
    max: 600,
    color: '#EF9F27',
    riskLabel: 'Moderate',
    shortDescription: 'Mixed field health — some strengths with material gaps to close.',
  },
  {
    id: 'good',
    name: 'Good',
    min: 600,
    max: 750,
    color: '#639922',
    riskLabel: 'Low',
    shortDescription: 'Solid field-health signals across most pillars on this holding.',
  },
  {
    id: 'excellent',
    name: 'Excellent',
    min: 750,
    max: 900,
    color: '#1D9E75',
    riskLabel: 'Very low',
    shortDescription: 'Strong land and crop condition across the assessed plots.',
  },
];

/** Convert internal 0–100 index → displayed KBS (300–900). */
export function toKbsScore(score0to100: number | null | undefined): number | null {
  if (score0to100 == null || !Number.isFinite(score0to100)) return null;
  const mapped = 300 + (score0to100 / 100) * 600;
  return clampKbs(Math.round(mapped));
}

export function clampKbs(n: number): number {
  return Math.min(KBS_MAX, Math.max(KBS_MIN, n));
}

export function kbsBandForScore(kbs: number | null | undefined): KbsBand | null {
  if (kbs == null || !Number.isFinite(kbs)) return null;
  const s = clampKbs(kbs);
  // Top of Excellent inclusive
  for (let i = 0; i < KBS_BANDS.length; i++) {
    const b = KBS_BANDS[i];
    const last = i === KBS_BANDS.length - 1;
    if (s >= b.min && (last ? s <= b.max : s < b.max)) return b;
  }
  return KBS_BANDS[KBS_BANDS.length - 1];
}

/** Position on semicircle: 0 = left (300), 1 = right (900). */
export function kbsNormalized(kbs: number): number {
  return (clampKbs(kbs) - KBS_MIN) / (KBS_MAX - KBS_MIN);
}

export function subScoreBarColor(score0to100: number): string {
  if (score0to100 < 55) return '#dc2626'; // danger
  if (score0to100 < 65) return '#d97706'; // warning
  return '#16a34a'; // success
}

export function riskPillClass(riskLabel: KbsBand['riskLabel']): string {
  switch (riskLabel) {
    case 'High':
      return 'bg-red-50 text-red-800 border-red-200';
    case 'Moderate':
      return 'bg-amber-50 text-amber-900 border-amber-200';
    case 'Low':
      return 'bg-emerald-50 text-emerald-800 border-emerald-200';
    case 'Very low':
      return 'bg-teal-50 text-teal-800 border-teal-200';
  }
}

/** Soft tinted surface for Overall risk card by band. */
export function bandCardSurface(band: KbsBand | null): {
  background: string;
  border: string;
  accent: string;
} {
  if (!band) {
    return { background: '#F5F2EB', border: '#E4DFD4', accent: '#78716c' };
  }
  switch (band.id) {
    case 'poor':
      return { background: 'linear-gradient(160deg, #FEF2F2 0%, #FEE2E2 100%)', border: '#FECACA', accent: band.color };
    case 'fair':
      return { background: 'linear-gradient(160deg, #FFFBEB 0%, #FEF3C7 100%)', border: '#FDE68A', accent: band.color };
    case 'good':
      return { background: 'linear-gradient(160deg, #F0FDF4 0%, #DCFCE7 100%)', border: '#BBF7D0', accent: band.color };
    case 'excellent':
      return { background: 'linear-gradient(160deg, #F0FDFA 0%, #CCFBF1 100%)', border: '#99F6E4', accent: band.color };
  }
}
