/**
 * Chart palettes — validated, not chosen.
 *
 * Every set here was run through the data-viz palette validator against this
 * app's actual surface (#F5F2EB), not the validator's default white. A palette
 * validated on white is not validated for this app. Re-run before changing any
 * value; the commands are in FRONTEND-ENHANCEMENTS.md Appendix A.
 *
 * Colour is assigned by the JOB the data does, never by what looks nice:
 *
 *   magnitude  → one hue, light→dark          (VEGETATION)
 *   identity   → fixed-order categorical, ≤3  (SERIES)
 *   polarity   → two hues + neutral midpoint  (DIVERGING)
 *   state      → the KBS bands                (see lib/kbsScore.ts)
 *
 * For risk state, import from `kbsScore.ts` instead. Nothing in this file is a
 * risk colour, and reusing a series hue for a status would let a chart series
 * impersonate a verdict.
 */

/**
 * Vegetation / NDVI — sequential, one hue (spread 19°), lightness monotone.
 *
 * Green is not decoration here: it matches how anyone who has looked at an
 * NDVI raster already reads the image. Do not substitute viridis.
 *
 * CONTINUOUS use (area fills, rasters, choropleths): use the whole ramp. The
 * light end is *allowed* to recede into the cream — near-zero NDVI should look
 * like bare ground.
 */
export const VEGETATION = [
  '#DDEAC4', // ~0.10 NDVI
  '#BAD795', // ~0.20
  '#94C267', // ~0.30
  '#6EAB3E', // ~0.40
  '#4C9028', // ~0.55
  '#31741F', // ~0.70
  '#1D5717', // ~0.85
] as const;

/** The mid step — the default single-series line colour. */
export const VEGETATION_LINE = VEGETATION[4];

/**
 * DISCRETE ordered marks — legend swatches, calendar cells, category dots.
 *
 * Starts at a darker step than VEGETATION because a discrete chip must clear
 * 2:1 against the surface to be seen at all. `#94C267` measures 1.85:1 and is
 * therefore fine as a continuous fill but not as a standalone swatch.
 *
 * Validator: monotone L, all adjacent gaps ≥ 0.06, light end 2.19:1, hue
 * spread 15° — ALL CHECKS PASS.
 */
export const VEGETATION_CHIPS = [
  '#8AB24F',
  '#67A03C',
  '#468526',
  '#2D6A1E',
  '#194E16',
] as const;

/**
 * Categorical — telling SOURCES apart (optical vs radar vs modelled).
 *
 * Fixed order, never cycled. Validator on cream, all-pairs: worst CVD ΔE 9.2,
 * worst normal-vision ΔE 24.0 — ALL CHECKS PASS. Two slots carry a contrast
 * WARN, so direct labels are required, not optional.
 *
 * HARD CAP AT THREE. A fourth series does not get a fourth hue — it folds into
 * "Other", or the chart becomes small multiples. Generating a 9th hue produces
 * something indistinguishable from an existing one under colour-vision
 * deficiency and breaks every check at once.
 */
export const SERIES = ['#2A78D6', '#EB6834', '#1BAF7A'] as const;

/**
 * Diverging — above/below a baseline. Rainfall vs seasonal normal, score delta.
 *
 * Warm/cool poles that read as opposite, with a NEUTRAL GREY midpoint. Never a
 * hue at the midpoint: the middle of a diverging scale means "nothing", and a
 * colour there implies it means something.
 */
export const DIVERGING = {
  low: '#2A78D6',
  mid: '#E4DFD4',
  high: '#B93A28',
} as const;

/** Chart chrome. Recessive by design — the data is the ink, not the frame. */
export const CHROME = {
  grid: '#E8E4DB',
  axis: '#D6D0C4',
  label: '#78716C',
  labelStrong: '#57534E',
  /** No observation. Never interpolate across one — draw the gap. */
  missing: '#EFEBE1',
} as const;

/** Scale a 0–1 value onto the continuous vegetation ramp. */
export function vegetationAt(value01: number): string {
  if (!Number.isFinite(value01)) return CHROME.missing;
  const t = Math.max(0, Math.min(1, value01));
  return VEGETATION[Math.min(VEGETATION.length - 1, Math.round(t * (VEGETATION.length - 1)))];
}
