/**
 * Krishi Bhoomi Score (KBS) — the single source of score→colour in this app.
 * Internal pipeline scores stay 0–100; headline display uses 300–900.
 *
 * ONE MAPPING, ONE MEANING
 * ────────────────────────
 * Before this file was consolidated there were four competing score→colour
 * ramps, and an index of 68 rendered amber, green and "Good" simultaneously
 * depending on which component drew it. Everything that turns a score into a
 * colour now goes through `bandForIndex` and its derived helpers. Do not add
 * an inline `score > 70 ? green : amber` anywhere — that is how this started.
 *
 * COLOUR IS NEVER THE ONLY CHANNEL
 * ────────────────────────────────
 * A red/amber/green scale is structurally unsafe under protanopia — no
 * re-stepping fixes it, because the amber↔green pair collapses. So every band
 * is rendered with its NAME, and on the gauge with its POSITION on the arc.
 * A bare coloured chip with no adjacent word is forbidden. `bandChipStyle`
 * exists to style a chip that already contains the band or risk label.
 *
 * THE HEXES ARE VALIDATED, NOT CHOSEN
 * ───────────────────────────────────
 * The four `color` values below pass every check of the data-viz palette
 * validator against this app's cream surface (#F5F2EB):
 *
 *   Lightness band  PASS (all inside L 0.43–0.77)
 *   Chroma floor    PASS (all >= 0.1)
 *   CVD separation  PASS (worst adjacent Good↔Fair ΔE 10.2, protan)
 *   Normal vision   PASS (worst adjacent Excellent↔Good ΔE 15.2)
 *   Contrast        WARN Fair at 1.92:1 → relief is the mandatory label
 *
 * The previous set failed: Good #639922 ↔ Excellent #1D9E75 measured ΔE 8.8
 * for *normal* colour vision, so the two bands that decide fundability were
 * not reliably distinguishable. Re-run the validator against #F5F2EB before
 * changing any hex here.
 *
 * `ink` is the text-safe step of each band (>= 5:1 on cream and on the band's
 * own surface). `color` is for marks only — Fair at 1.92:1 must never be text.
 */

export const KBS_MIN = 300;
export const KBS_MAX = 900;

export type KbsBandId = 'poor' | 'fair' | 'good' | 'excellent';

/** Backend `risk_category` enum. Higher risk = lower band. */
export type RiskCategoryValue = 'VERY_HIGH' | 'HIGH' | 'MEDIUM' | 'LOW';

export type KbsBand = {
  id: KbsBandId;
  name: string;
  /** KBS bounds (300–900). */
  min: number;
  max: number;
  /** Equivalent bounds on the internal 0–100 index. */
  indexMin: number;
  indexMax: number;
  /** Mark colour — arcs, bars, map fills. Never text. */
  color: string;
  /** Text-safe step of the same hue. Use for any label carrying band identity. */
  ink: string;
  /** Soft tinted chip/card surface. */
  surface: string;
  /** Hairline for the tinted surface. */
  border: string;
  /** Human risk label shown beside the colour. */
  riskLabel: 'High' | 'Moderate' | 'Low' | 'Very low';
  /** Backend risk_category this band corresponds to. */
  riskCategory: RiskCategoryValue;
  shortDescription: string;
};

/** Four equal bands of 150 KBS points / 25 index points / 45° on the gauge. */
export const KBS_BANDS: KbsBand[] = [
  {
    id: 'poor',
    name: 'Poor',
    min: 300,
    max: 450,
    indexMin: 0,
    indexMax: 25,
    color: '#B93A28',
    ink: '#9A2E1F',
    surface: '#FAE8E4',
    border: '#F2D2CB',
    riskLabel: 'High',
    riskCategory: 'VERY_HIGH',
    shortDescription: 'Land and crop condition is weak across key field-health signals.',
  },
  {
    id: 'fair',
    name: 'Fair',
    min: 450,
    max: 600,
    indexMin: 25,
    indexMax: 50,
    color: '#E5A614',
    ink: '#7A5405',
    surface: '#FCF0D9',
    border: '#F5E2B8',
    riskLabel: 'Moderate',
    riskCategory: 'HIGH',
    shortDescription: 'Mixed field health — some strengths with material gaps to close.',
  },
  {
    id: 'good',
    name: 'Good',
    min: 600,
    max: 750,
    indexMin: 50,
    indexMax: 75,
    color: '#6B9418',
    ink: '#4C6C11',
    surface: '#EDF4DE',
    border: '#DCE8C4',
    riskLabel: 'Low',
    riskCategory: 'MEDIUM',
    shortDescription: 'Solid field-health signals across most pillars on this holding.',
  },
  {
    id: 'excellent',
    name: 'Excellent',
    min: 750,
    max: 900,
    indexMin: 75,
    indexMax: 100,
    color: '#00734F',
    ink: '#006446',
    surface: '#DFF0E9',
    border: '#C4E2D6',
    riskLabel: 'Very low',
    riskCategory: 'LOW',
    shortDescription: 'Strong land and crop condition across the assessed plots.',
  },
];

/** Neutral treatment for "no band" — unscored, refused, or insufficient data. */
export const NO_BAND = {
  color: '#A8A29E',
  ink: '#57534E',
  surface: '#F0EDE6',
  border: '#E4DFD4',
} as const;

/** Convert internal 0–100 index → displayed KBS (300–900). */
export function toKbsScore(score0to100: number | null | undefined): number | null {
  if (score0to100 == null || !Number.isFinite(score0to100)) return null;
  return clampKbs(Math.round(300 + (score0to100 / 100) * 600));
}

export function clampKbs(n: number): number {
  return Math.min(KBS_MAX, Math.max(KBS_MIN, n));
}

export function kbsBandForScore(kbs: number | null | undefined): KbsBand | null {
  if (kbs == null || !Number.isFinite(kbs)) return null;
  const s = clampKbs(kbs);
  for (let i = 0; i < KBS_BANDS.length; i++) {
    const b = KBS_BANDS[i];
    const last = i === KBS_BANDS.length - 1;
    if (s >= b.min && (last ? s <= b.max : s < b.max)) return b;
  }
  return KBS_BANDS[KBS_BANDS.length - 1];
}

/**
 * Band for an internal 0–100 index. The canonical entry point — everything
 * that colours a score resolves through here, so the thresholds live in
 * exactly one place.
 */
export function bandForIndex(score0to100: number | null | undefined): KbsBand | null {
  return kbsBandForScore(toKbsScore(score0to100));
}

/**
 * THE score→colour function. Sub-index bars, pillar fills, waterfall segments.
 *
 * Returns a mark colour, so the caller is responsible for an adjacent label
 * (see the file header). For text use `bandForIndex(v)?.ink`.
 */
export function scoreColor(score0to100: number | null | undefined): string {
  return bandForIndex(score0to100)?.color ?? NO_BAND.color;
}

/** Map the backend `risk_category` enum onto the same four bands. */
export function bandForRiskCategory(risk: string | null | undefined): KbsBand | null {
  const r = String(risk || '').toUpperCase().replace(/[\s-]/g, '_');
  if (!r) return null;
  // VERY_HIGH must be tested before HIGH — it contains it as a substring.
  if (r.includes('VERY_HIGH')) return KBS_BANDS[0];
  if (r === 'HIGH') return KBS_BANDS[1];
  if (r === 'MEDIUM' || r === 'MODERATE') return KBS_BANDS[2];
  if (r === 'LOW') return KBS_BANDS[3];
  return null;
}

/**
 * Inline style for a chip that ALREADY CONTAINS the band or risk label.
 * Replaces the old `riskPillClass` and `riskBgClass`, which disagreed with
 * each other and with the gauge.
 */
export function bandChipStyle(band: KbsBand | null): {
  background: string;
  color: string;
  borderColor: string;
} {
  const b = band ?? NO_BAND;
  return { background: b.surface, color: b.ink, borderColor: b.border };
}

/**
 * Soft band wash for a card.
 *
 * A gradient is legitimate here because the card's size encodes nothing — the
 * tint reinforces the label rather than standing in for a value. Never put a
 * gradient on a bar, arc, or anything whose extent means a number.
 */
export function bandCardSurface(band: KbsBand | null): {
  background: string;
  border: string;
  accent: string;
  ink: string;
} {
  const b = band ?? NO_BAND;
  return {
    background: `linear-gradient(160deg, ${b.surface} 0%, #FFFFFF 100%)`,
    border: b.border,
    accent: b.color,
    ink: b.ink,
  };
}

/** Position on the semicircle: 0 = left (300), 1 = right (900). */
export function kbsNormalized(kbs: number): number {
  return (clampKbs(kbs) - KBS_MIN) / (KBS_MAX - KBS_MIN);
}
