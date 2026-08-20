'use client';

import type { AssessmentPayload, Footprint } from '../../types/assessment';
import { formatGateMultiplier } from '../../lib/formatRisk';

/**
 * The qualifiers that travel with a score.
 *
 * A number with a caveat attached is a different number from one without, and
 * the caveat has to be as visible as the figure it qualifies. These are not
 * tooltips.
 */

type Tone = 'neutral' | 'caution' | 'serious';

const TONES: Record<Tone, { bg: string; border: string; ink: string }> = {
  neutral: { bg: '#F0EDE6', border: '#E4DFD4', ink: '#57534E' },
  caution: { bg: '#FCF0D9', border: '#F5E2B8', ink: '#7A5405' },
  serious: { bg: '#FAE8E4', border: '#F2D2CB', ink: '#9A2E1F' },
};

function Glyph({ tone }: { tone: Tone }) {
  // Status colour never carries meaning alone — every badge pairs a glyph and
  // a word with its tint.
  if (tone === 'serious') {
    return (
      <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 shrink-0" aria-hidden fill="currentColor">
        <path d="M8 1.5 15 14H1L8 1.5Zm0 4.2a.7.7 0 0 0-.7.75l.2 3.1a.5.5 0 0 0 1 0l.2-3.1A.7.7 0 0 0 8 5.7Zm0 5.3a.8.8 0 1 0 0 1.6.8.8 0 0 0 0-1.6Z" />
      </svg>
    );
  }
  if (tone === 'caution') {
    return (
      <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 shrink-0" aria-hidden fill="currentColor">
        <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm0 3.2a.75.75 0 0 1 .75.8l-.2 3.4a.55.55 0 0 1-1.1 0l-.2-3.4A.75.75 0 0 1 8 4.2Zm0 6.1a.85.85 0 1 1 0 1.7.85.85 0 0 1 0-1.7Z" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5 shrink-0" aria-hidden fill="currentColor">
      <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.75 10.4h-1.5V7.2h1.5v4.2Zm-.75-5.6a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8Z" />
    </svg>
  );
}

export function ConfidenceBadge({
  label,
  tone = 'neutral',
  title,
}: {
  label: string;
  tone?: Tone;
  title?: string;
}) {
  const t = TONES[tone];
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-semibold"
      style={{ background: t.bg, borderColor: t.border, color: t.ink }}
      title={title}
    >
      <Glyph tone={tone} />
      {label}
    </span>
  );
}

/**
 * The confidence gate, stated as what it did rather than as a bare multiplier.
 *
 * "0.92" means nothing to a loan officer. "Score reduced 8% — limited
 * observation" is the same fact, readable.
 */
export function GateBadge({ gate }: { gate: number | null | undefined }) {
  if (typeof gate !== 'number' || !Number.isFinite(gate) || gate >= 0.995) return null;
  const cut = Math.round((1 - gate) * 100);
  return (
    <ConfidenceBadge
      label={`Score reduced ${cut}% — limited observation`}
      tone={cut >= 15 ? 'serious' : 'caution'}
      title={`Confidence gate ${formatGateMultiplier(gate)}. Applied multiplicatively to the raw index.`}
    />
  );
}

/**
 * Footprint substitution — the P0 case.
 *
 * The user is looking at a polygon. If it is not the polygon we measured, that
 * cannot be a footnote: every number on the page is about different ground
 * than the shape on screen.
 */
export function FootprintBanner({ footprint }: { footprint: Footprint | null | undefined }) {
  if (!footprint?.geometry_substituted) return null;

  return (
    <div
      role="status"
      className="flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5"
      style={{ background: TONES.serious.bg, borderColor: TONES.serious.border }}
    >
      <span style={{ color: TONES.serious.ink }} className="mt-0.5">
        <Glyph tone="serious" />
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-semibold" style={{ color: TONES.serious.ink }}>
          Scored over a substituted footprint — not the boundary shown.
        </p>
        <p className="text-[12px] text-ink-2 mt-0.5 leading-snug">
          {footprint.note ||
            'The supplied boundary failed validation, so measurement fell back to a substitute. Re-draw the boundary before relying on this score.'}
          {footprint.geometry_source && (
            <>
              {' '}
              <span className="font-mono text-[11px] text-ink-muted">
                source: {footprint.geometry_source}
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

/**
 * The qualifier strip that sits under a score: gate, footprint, land-cover
 * flag. Renders nothing when the score is unqualified — an empty "all clear"
 * chip is noise, and worse, it trains the reader to stop looking here.
 */
export function ConfidenceStrip({ data }: { data: AssessmentPayload | null }) {
  if (!data) return null;

  const footprint = data.risk_assessment?.footprint ?? null;
  const lc = data.land_cover;
  const viability = data.parcel_viability;

  const flagged = lc?.outcome === 'flag';
  const marginal = viability?.outcome === 'marginal';

  if (!flagged && !marginal && !footprint?.geometry_substituted) return null;

  return (
    <div className="space-y-2">
      <FootprintBanner footprint={footprint} />
      {(flagged || marginal) && (
        <div className="flex flex-wrap gap-1.5">
          {flagged && (
            <ConfidenceBadge
              label={`Land cover flagged${lc?.class ? ` — ${lc.class.toLowerCase()}` : ''}`}
              tone="caution"
              title={lc?.reason}
            />
          )}
          {marginal && (
            <ConfidenceBadge
              label="Parcel measurable but small"
              tone="caution"
              title={viability?.reason}
            />
          )}
        </div>
      )}
    </div>
  );
}
