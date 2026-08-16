/**
 * Map mark colours — the single source for anything drawn on a Leaflet layer.
 *
 * Leaflet takes plain colour strings, not CSS classes, so these cannot be
 * Tailwind utilities. Keeping them here rather than inline means a boundary
 * colour is defined once instead of in each of the four map components, and
 * that the map agrees with the rest of the app.
 *
 * These are MARK colours. Any label describing one is text and must use an
 * ink token (see globals.css) — several of these fail text contrast on cream.
 *
 * Provenance and confidence are encoded by DASH PATTERN, not by hue: a
 * measured boundary and a declared one are the same kind of thing observed
 * with different certainty, and a hue change would imply they are different
 * quantities. Same principle as the trajectory chart's provenance encoding.
 */

export const MAP_COLORS = {
  /** Assessed / confirmed parcel boundary. */
  boundary: '#15803D',
  boundaryFill: '#86EFAC',
  /** Boundary the user is actively drawing or editing. */
  draw: '#2A78D6',
  drawFill: '#B6DEF7',
  /** Parcel with a warning — marginal viability, tenure discount, skipped. */
  warn: '#B4842A',
  warnFill: '#F5E2B8',
  /** Focused / selected parcel. */
  focus: '#B93A28',
  /** Base tile fallback while imagery loads. */
  tileBackdrop: '#E8E4DB',
} as const;

/** Solid = measured footprint. Dashed = declared but not verified. */
export const MAP_DASH = {
  measured: undefined as string | undefined,
  declared: '6 4',
  editing: '4 3',
} as const;

export const MAP_WEIGHTS = {
  boundary: 2,
  focused: 3,
} as const;

/** Leaflet `pathOptions` for a parcel in a given state. */
export function parcelStyle(
  state: 'assessed' | 'warn' | 'focused' | 'declared' | 'drawing'
): {
  color: string;
  weight: number;
  fillColor: string;
  fillOpacity: number;
  dashArray?: string;
} {
  switch (state) {
    case 'warn':
      return {
        color: MAP_COLORS.warn,
        weight: MAP_WEIGHTS.boundary,
        fillColor: MAP_COLORS.warnFill,
        fillOpacity: 0.18,
      };
    case 'focused':
      return {
        color: MAP_COLORS.focus,
        weight: MAP_WEIGHTS.focused,
        fillColor: MAP_COLORS.boundaryFill,
        fillOpacity: 0.22,
      };
    case 'declared':
      // The boundary as submitted, when it is NOT what we measured over.
      // Greyed and dashed so it can never be mistaken for the scored footprint.
      return {
        color: '#A8A29E',
        weight: MAP_WEIGHTS.boundary,
        fillColor: '#A8A29E',
        fillOpacity: 0.06,
        dashArray: MAP_DASH.declared,
      };
    case 'drawing':
      return {
        color: MAP_COLORS.draw,
        weight: MAP_WEIGHTS.boundary,
        fillColor: MAP_COLORS.drawFill,
        fillOpacity: 0.2,
        dashArray: MAP_DASH.editing,
      };
    case 'assessed':
    default:
      return {
        color: MAP_COLORS.boundary,
        weight: MAP_WEIGHTS.boundary,
        fillColor: MAP_COLORS.boundaryFill,
        fillOpacity: 0.18,
      };
  }
}
