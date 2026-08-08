'use client';

import { KBS_BANDS, KBS_MAX, kbsNormalized, type KbsBand } from '../../lib/kbsScore';

/**
 * Semicircular speedometer: 180° arc, 4 equal bands, needle to current KBS.
 */
export function KbsGauge({
  score,
  band,
  placeholder,
  compact,
}: {
  score: number | null;
  band: KbsBand | null;
  placeholder?: boolean;
  compact?: boolean;
}) {
  const cx = 160;
  const cy = compact ? 132 : 158;
  const r = compact ? 92 : 110;
  const stroke = compact ? 14 : 18;

  const polar = (t: number, radius: number) => {
    const angle = Math.PI * (1 - t);
    return {
      x: cx + radius * Math.cos(angle),
      y: cy - radius * Math.sin(angle),
    };
  };

  const arcPath = (t0: number, t1: number) => {
    const a = polar(t0, r);
    const b = polar(t1, r);
    const large = t1 - t0 > 0.5 ? 1 : 0;
    return `M ${a.x} ${a.y} A ${r} ${r} 0 ${large} 1 ${b.x} ${b.y}`;
  };

  const needleT = placeholder || score == null ? 0 : kbsNormalized(score);
  const needleTip = polar(needleT, r - stroke / 2 - 4);
  const hub = { x: cx, y: cy };
  const vbH = compact ? 175 : 220;
  const labelR = r + (compact ? 22 : 28);

  return (
    <div className={`w-full mx-auto ${compact ? 'max-w-none' : 'max-w-md'}`}>
      <svg
        viewBox={`0 0 320 ${vbH}`}
        className="w-full h-auto"
        role="img"
        aria-label={
          score != null && band
            ? `Krishi Bhoomi Score ${score} out of ${KBS_MAX}, ${band.name}`
            : 'Krishi Bhoomi Score gauge'
        }
      >
        {KBS_BANDS.map((b, i) => {
          const t0 = i / 4;
          const t1 = (i + 1) / 4;
          return (
            <path
              key={b.id}
              d={arcPath(t0, t1)}
              fill="none"
              stroke={placeholder ? '#E8E4DB' : b.color}
              strokeWidth={stroke}
              strokeLinecap="butt"
              opacity={placeholder ? 0.7 : 1}
            />
          );
        })}

        {!placeholder &&
          KBS_BANDS.map((b, i) => {
            const mid = (i + 0.5) / 4;
            const p = polar(mid, labelR);
            return (
              <text
                key={`lbl-${b.id}`}
                x={p.x}
                y={p.y}
                textAnchor="middle"
                className="fill-stone-500"
                style={{ fontSize: compact ? 8 : 9, fontWeight: 600 }}
              >
                <tspan x={p.x} dy="0">
                  {b.name}
                </tspan>
                <tspan
                  x={p.x}
                  dy={compact ? 9 : 11}
                  style={{ fontWeight: 500, fontSize: compact ? 7 : 8 }}
                  className="fill-stone-400"
                >
                  {b.min}–{b.max}
                </tspan>
              </text>
            );
          })}

        {!placeholder && score != null && (
          <g>
            <line
              x1={hub.x}
              y1={hub.y}
              x2={needleTip.x}
              y2={needleTip.y}
              stroke="#1c1917"
              strokeWidth={2.25}
              strokeLinecap="round"
            />
            <circle cx={hub.x} cy={hub.y} r={6} fill="#1c1917" />
            <circle cx={hub.x} cy={hub.y} r={2.5} fill="#F5F2EB" />
            <circle
              cx={needleTip.x}
              cy={needleTip.y}
              r={3}
              fill={band?.color || '#1c1917'}
              stroke="#fff"
              strokeWidth={1}
            />
          </g>
        )}

        <text
          x={polar(0, r - 28).x}
          y={cy + 14}
          textAnchor="middle"
          className="fill-stone-400"
          style={{ fontSize: 9 }}
        >
          300
        </text>
        <text
          x={polar(1, r - 28).x}
          y={cy + 14}
          textAnchor="middle"
          className="fill-stone-400"
          style={{ fontSize: 9 }}
        >
          900
        </text>
      </svg>

      <div className={`text-center ${compact ? '-mt-1' : '-mt-2'}`}>
        {placeholder || score == null ? (
          <>
            <p className={`${compact ? 'text-2xl' : 'text-3xl'} font-bold text-stone-400`}>—</p>
            <p className="text-[11px] text-stone-400 mt-0.5">out of {KBS_MAX}</p>
          </>
        ) : (
          <>
            <p
              className={`${compact ? 'text-3xl' : 'text-4xl'} font-bold font-mono tabular-nums tracking-tight leading-none`}
              style={{ color: band?.color || '#1c1917' }}
            >
              {score}
            </p>
            <p className="text-xs text-stone-600 mt-1">
              {score} · out of {KBS_MAX}
              {band ? ` · ${band.name}` : ''}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
