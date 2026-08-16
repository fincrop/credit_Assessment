'use client';

import type { WeatherAnalysis } from '../../types/assessment';
import { readWeatherIndicator } from '../../lib/formatRisk';
import { DIVERGING, CHROME } from '../../lib/vizPalette';
import { ChartFrame, Legend, LegendItem, type TableColumn } from './ChartFrame';

/**
 * Weather against normal, per season.
 *
 * SPI and SPEI are standardised anomalies — already expressed in standard
 * deviations from a long-run normal, centred on zero. That makes them the
 * one weather figure that belongs on a DIVERGING scale, and the reason this
 * panel replaces the grid of eight stat cards: "SPI −1.4" means nothing to a
 * loan officer, while a bar reaching left of centre past the drought marker
 * reads instantly.
 *
 * Diverging rules, per the palette: two hues, a NEUTRAL grey midpoint (the
 * middle of the scale means "normal", and a colour there would imply it means
 * something), and equal weight to both arms.
 */

type Row = {
  label: string;
  spi: number | null;
  spei: number | null;
  dry: number | null;
  wet: number | null;
};

const TABLE_COLUMNS: TableColumn<Row>[] = [
  { header: 'Season', cell: (r) => r.label },
  { header: 'SPI', numeric: true, cell: (r) => (r.spi == null ? '—' : r.spi.toFixed(2)) },
  { header: 'SPEI', numeric: true, cell: (r) => (r.spei == null ? '—' : r.spei.toFixed(2)) },
  { header: 'Dry spell', numeric: true, cell: (r) => (r.dry == null ? '—' : `${r.dry} d`) },
  { header: 'Wet spell', numeric: true, cell: (r) => (r.wet == null ? '—' : `${r.wet} d`) },
];

/** ±3σ covers essentially everything; fixing it keeps seasons comparable. */
const LIMIT = 3;

function AnomalyBar({ value }: { value: number }) {
  const clamped = Math.max(-LIMIT, Math.min(LIMIT, value));
  const halfPct = (Math.abs(clamped) / LIMIT) * 50;
  const negative = clamped < 0;

  return (
    <div className="relative h-4 rounded-sm" style={{ background: CHROME.grid }}>
      {/* Flat fill: the bar's LENGTH is the value, so a gradient along it
          would make the same anomaly read differently at each end. */}
      <div
        className="absolute top-0 bottom-0"
        style={{
          background: negative ? DIVERGING.low : DIVERGING.high,
          width: `${halfPct}%`,
          left: negative ? `${50 - halfPct}%` : '50%',
        }}
      />
      {/* Neutral midpoint — "normal" is the absence of anomaly, so it is grey. */}
      <div
        className="absolute top-[-2px] bottom-[-2px] w-[2px]"
        style={{ left: 'calc(50% - 1px)', background: CHROME.axis }}
      />
    </div>
  );
}

export function WeatherAnomalyChart({
  weather,
}: {
  weather: WeatherAnalysis | null | undefined;
}) {
  const seasons = weather?.seasonal_weather ?? [];

  const rows: Row[] = seasons
    .map((s) => {
      const ind = s.weather_indicators;
      if (!ind) return null;
      const num = (v: unknown) =>
        typeof v === 'number' && Number.isFinite(v) ? v : null;
      return {
        label: [s.season, s.year].filter(Boolean).join(' ') || 'Season',
        spi: num(ind.spi_like),
        spei: num(ind.spei_like),
        dry: num(readWeatherIndicator(ind, 'max_dry_spell_days', 'dry_spell_max_days')),
        wet: num(readWeatherIndicator(ind, 'max_wet_spell_days', 'wet_spell_max_days')),
      };
    })
    .filter((r): r is Row => r !== null);

  const plottable = rows.filter((r) => r.spi != null || r.spei != null);

  if (plottable.length === 0) {
    return (
      <ChartFrame
        title="Weather against normal"
        subtitle="Rainfall and moisture anomalies across the assessed seasons."
        empty={
          rows.length > 0
            ? 'Seasonal weather was recorded, but without standardised anomaly indices there is no meaningful baseline to plot against. The raw figures appear in the weather section.'
            : 'No seasonal weather indicators were stored for this assessment.'
        }
      />
    );
  }

  return (
    <ChartFrame
      title="Weather against normal"
      subtitle="Standardised anomalies: zero is the long-run normal for this location, and each step is one standard deviation."
      tableRows={rows}
      tableColumns={TABLE_COLUMNS}
      legend={
        <Legend>
          <LegendItem color={DIVERGING.low} label="Drier than normal" />
          <LegendItem color={DIVERGING.high} label="Wetter than normal" />
        </Legend>
      }
      note={
        <>
          Beyond about ±1.5 is where a season starts to be unusual. This shows what
          the weather did, not what it cost — the effect on the score runs through
          the weather driver, which also accounts for when in the cycle it landed.
        </>
      }
    >
      <div className="space-y-3">
        {plottable.map((r, i) => {
          const value = r.spei ?? r.spi ?? 0;
          return (
            <div key={i}>
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className="text-[12px] text-ink-2 truncate">{r.label}</span>
                <span className="text-[12px] font-mono tabular-nums text-ink shrink-0">
                  {value > 0 ? '+' : ''}
                  {value.toFixed(2)}
                  <span className="text-ink-muted ml-1 text-[10px]">
                    {r.spei != null ? 'SPEI' : 'SPI'}
                  </span>
                </span>
              </div>
              <AnomalyBar value={value} />
              {(r.dry != null || r.wet != null) && (
                <p className="text-[10px] text-ink-muted mt-1">
                  {r.dry != null && `longest dry spell ${r.dry} d`}
                  {r.dry != null && r.wet != null && ' · '}
                  {r.wet != null && `longest wet spell ${r.wet} d`}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex justify-between text-[10px] text-ink-muted font-mono mt-2">
        <span>−{LIMIT}σ dry</span>
        <span>normal</span>
        <span>+{LIMIT}σ wet</span>
      </div>
    </ChartFrame>
  );
}
