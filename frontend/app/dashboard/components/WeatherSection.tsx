'use client';

import type { AssessmentPayload, CropCycle, WeatherIndicators } from '../../types/assessment';
import type { ReportNdviTrajectory } from '../../types/report';
import { resolveCropCycles } from '../../lib/ndviCycles';
import { prettySeasonTitle, yearOfCycle } from '../../lib/seasonLabels';
import { readWeatherIndicator } from '../../lib/formatRisk';

type ExtremeEvent = Record<string, unknown>;

function num(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  const n = typeof v === 'string' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

function prettyDate(value: string | null | undefined): string | null {
  if (!value || value === '—') return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    const sliced = String(value).slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(sliced) ? sliced : null;
  }
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function prettyEventType(raw: unknown): string {
  const key = String(raw || '').toLowerCase();
  if (key.includes('heat')) return 'Heat';
  if (key.includes('cold')) return 'Cold';
  if (key.includes('drought') || key === 'dry') return 'Dry spell';
  if (key.includes('flood')) return 'Flood';
  if (key.includes('rain')) return 'Heavy rain';
  if (!key || key === 'unknown') return 'Weather event';
  return String(raw).replace(/_/g, ' ');
}

function riskBand(score: number | null): {
  word: string;
  why: string;
  ink: string;
  surface: string;
  border: string;
} {
  if (score == null) {
    return {
      word: 'Unknown',
      why: 'No weather risk score was stored for this plot.',
      ink: '#57534E',
      surface: '#F0EDE6',
      border: '#E4DFD4',
    };
  }
  if (score < 20) {
    return {
      word: 'Calm',
      why: 'Weather stayed within a manageable range for the seasons we watched.',
      ink: '#006446',
      surface: '#DFF0E9',
      border: '#C4E2D6',
    };
  }
  if (score < 40) {
    return {
      word: 'Watch',
      why: 'Some harsh spells showed up, but they were not extreme across every season.',
      ink: '#7A5405',
      surface: '#FCF0D9',
      border: '#F5E2B8',
    };
  }
  return {
    word: 'Stress',
    why: 'Weather was hard on the crop in the seasons we watched.',
    ink: '#9A2E1F',
    surface: '#FAE8E4',
    border: '#F2D2CB',
  };
}

function severityStyle(raw: unknown): { word: string; ink: string; surface: string; border: string } {
  const s = String(raw || '').toLowerCase();
  if (s === 'high' || s === 'extreme') {
    return { word: s === 'extreme' ? 'Severe' : 'High', ink: '#9A2E1F', surface: '#FAE8E4', border: '#F2D2CB' };
  }
  if (s === 'low') {
    return { word: 'Low', ink: '#006446', surface: '#DFF0E9', border: '#C4E2D6' };
  }
  return { word: 'Medium', ink: '#7A5405', surface: '#FCF0D9', border: '#F5E2B8' };
}

function eventKind(raw: unknown): 'heat' | 'cold' | 'dry' | 'rain' | 'flood' | 'other' {
  const key = String(raw || '').toLowerCase();
  if (key.includes('heat')) return 'heat';
  if (key.includes('cold')) return 'cold';
  if (key.includes('drought') || key === 'dry') return 'dry';
  if (key.includes('flood')) return 'flood';
  if (key.includes('rain')) return 'rain';
  return 'other';
}

function WxIcon({ kind, className }: { kind: 'heat' | 'cold' | 'dry' | 'rain' | 'flood' | 'alert' | 'leaf' | 'other'; className?: string }) {
  const cls = className || 'w-4 h-4';
  switch (kind) {
    case 'heat':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      );
    case 'cold':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 2v20M12 8l-4-3M12 8l4-3M12 16l-4 3M12 16l4 3M5 9l14 6M5 15l14-6" />
        </svg>
      );
    case 'dry':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 3c4 5.5 6 9 6 12a6 6 0 1 1-12 0c0-3 2-6.5 6-12z" />
          <path d="M10 14h4" />
        </svg>
      );
    case 'rain':
    case 'flood':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M7 16a4 4 0 1 1 1.2-7.8A5 5 0 0 1 18 11a3.5 3.5 0 0 1-.2 7H7z" />
          <path d="M8.5 19.5l-1 2.5M12 19v3M16 19.5l1 2.5" />
        </svg>
      );
    case 'leaf':
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M5 19c4-2 6-6 7-12 4 2 6 6 7 12" />
          <path d="M12 7v12" />
        </svg>
      );
    default:
      return (
        <svg className={cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M12 9v4M12 17h.01" />
          <path d="M10.3 4.3 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z" />
        </svg>
      );
  }
}

function StatTile({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: string;
}) {
  return (
    <div className="h-full rounded-lg bg-paper/80 border border-rule px-4 py-3 flex flex-col">
      <p className="text-[10px] font-semibold text-ink-muted uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-1 leading-none" style={{ color: color || '#1C1917' }}>
        {value}
      </p>
      {hint ? (
        <p className="text-[11px] text-ink-muted mt-auto pt-1.5 leading-snug">{hint}</p>
      ) : (
        <span className="mt-auto" aria-hidden />
      )}
    </div>
  );
}

function Indicator({
  kind,
  label,
  value,
  hint,
  alert,
}: {
  kind: 'heat' | 'cold' | 'dry' | 'rain' | 'flood' | 'other';
  label: string;
  value: string;
  hint?: string;
  alert?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border px-3 py-2.5 ${
        alert ? 'bg-amber-50/80 border-amber-200' : 'bg-white/70 border-rule'
      }`}
    >
      <div className="flex items-center gap-1.5 text-stone-500">
        <WxIcon kind={kind} className={`w-3.5 h-3.5 ${alert ? 'text-amber-800' : 'text-stone-500'}`} />
        <p className="text-[10px] uppercase tracking-wider font-semibold">{label}</p>
      </div>
      <p className={`text-sm font-bold mt-1 ${alert ? 'text-amber-950' : 'text-stone-800'}`}>{value}</p>
      {hint && <p className="text-[11px] text-stone-500 mt-0.5 leading-snug">{hint}</p>}
    </div>
  );
}

function eventDate(ev: ExtremeEvent): string | null {
  return prettyDate(String(ev.date ?? ev.date_or_start ?? ev.start_date ?? ev.end_date ?? ''));
}

function freqCopy(freq: number | null, every: string, half: string): string | null {
  if (freq == null || freq <= 0.05) return null;
  if (freq >= 0.85) return every;
  if (freq >= 0.4) return half;
  return null;
}

function daysLabel(n: number | null, unit = 'days'): string {
  if (n == null) return '—';
  const rounded = Math.round(n);
  if (rounded === 1) return `1 ${unit.replace(/s$/, '')}`;
  return `${rounded} ${unit}`;
}

export function WeatherSection({
  data,
  ndviTrajectory,
}: {
  data: AssessmentPayload;
  ndviTrajectory?: ReportNdviTrajectory | null;
}) {
  const wa = data.weather_analysis;
  if (!wa) {
    return (
      <div className="bg-white rounded-xl border border-rule p-6">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Weather</p>
        <p className="text-sm text-stone-500 mt-2">No weather findings are stored for this plot yet.</p>
      </div>
    );
  }

  const cycles: CropCycle[] = resolveCropCycles(
    data.crop_cycles?.cycles ?? [],
    ndviTrajectory
  );
  const seasonal = wa.seasonal_weather ?? [];
  const cycleRisks = wa.cycle_risk_scores ?? [];
  const events: ExtremeEvent[] = wa.extreme_events ?? [];
  const risk = num(wa.weather_risk_score);
  const band = riskBand(risk);
  const nEvents = wa.total_extreme_events ?? events.length;
  const nCritical = wa.critical_stage_events ?? 0;
  const fwd = wa.forward_exposure ?? {};
  const bwd = wa.backward_resilience ?? {};
  const droughtFreq = num(fwd.drought_freq);
  const heatFreq = num(fwd.heat_freq);
  const floodFreq = num(fwd.flood_freq);
  const exposure = num(fwd.exposure_score);
  const resilience = num(bwd.mean_resilience_score);
  const tested = num(bwd.tested_cycles) ?? 0;

  const typeCounts = events.reduce<Record<string, number>>((acc, ev) => {
    const label = prettyEventType(ev.type);
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});
  const typeHint =
    Object.entries(typeCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, n]) => `${n} ${label.toLowerCase()}`)
      .slice(0, 3)
      .join(' · ') || 'No named alerts';

  const forwardBits = [
    freqCopy(droughtFreq, 'Every season had a long dry stretch.', 'About half the seasons had a long dry stretch.'),
    freqCopy(heatFreq, 'Heat was a problem in every season.', 'Heat showed up in about half the seasons.'),
    freqCopy(floodFreq, 'Heavy rain or flooding was a regular pattern.', 'Heavy rain showed up in some seasons.'),
  ].filter(Boolean) as string[];

  const goingForward =
    forwardBits.length > 0
      ? forwardBits.join(' ')
      : 'No strong weather pattern stands out from the seasons we watched.';

  const heldUp =
    tested <= 0 || resilience == null
      ? 'This plot has not been through hard weather in the window we watched, so we cannot yet say how the crop holds up under stress.'
      : resilience >= 70
        ? 'The crop stayed green even when the weather turned harsh.'
        : resilience >= 50
          ? 'The crop held up in mixed weather — some stress, some recovery.'
          : 'The crop struggled when the weather turned harsh.';

  const summaryParts: string[] = [];
  if (nEvents > 0) {
    summaryParts.push(
      `${nEvents} weather alert${nEvents === 1 ? '' : 's'} ${nEvents === 1 ? 'was' : 'were'} flagged.`
    );
  } else {
    summaryParts.push('No extreme weather alerts were flagged.');
  }
  if (nCritical > 0) {
    summaryParts.push(`${nCritical} landed at a sensitive growth stage.`);
  }
  summaryParts.push(band.why);

  type SeasonWeather = (typeof seasonal)[number];

  function matchSeasonWeather(
    cycle: CropCycle,
    index: number,
    used: Set<number>
  ): SeasonWeather | undefined {
    const label = String(cycle.season_label || cycle.season_type || '')
      .replace(/_/g, ' ')
      .trim()
      .toLowerCase();
    const year = yearOfCycle(cycle);
    const cycleId = cycle.cycle_id != null ? String(cycle.cycle_id) : '';

    for (let j = 0; j < seasonal.length; j++) {
      if (used.has(j)) continue;
      const s = seasonal[j];
      const extra = s as { season_label?: unknown; cycle_id?: unknown };
      const sl =
        extra.season_label != null
          ? String(extra.season_label).replace(/_/g, ' ').trim().toLowerCase()
          : '';
      if (sl && label && sl === label) {
        used.add(j);
        return s;
      }
      if (cycleId && extra.cycle_id != null && String(extra.cycle_id) === cycleId) {
        used.add(j);
        return s;
      }
    }

    for (let j = 0; j < seasonal.length; j++) {
      if (used.has(j)) continue;
      const s = seasonal[j];
      const sy = typeof s.year === 'number' ? s.year : num(s.year) ?? undefined;
      const sn = String(s.season || '').toUpperCase();
      const ct = String(cycle.season_type || '').toUpperCase();
      if (year != null && sy === year && (!ct || sn.includes(ct) || ct.includes(sn))) {
        used.add(j);
        return s;
      }
    }

    if (index < seasonal.length && !used.has(index)) {
      used.add(index);
      return seasonal[index];
    }
    return undefined;
  }

  function buildWeatherRow(s: SeasonWeather | undefined, cycle: CropCycle | undefined, i: number) {
    const extra = (s || {}) as {
      cycle_id?: unknown;
      season_label?: unknown;
      season_type?: unknown;
      extreme_events?: ExtremeEvent[];
      year?: unknown;
      season?: unknown;
      weather_indicators?: WeatherIndicators;
    };
    const year =
      cycle != null
        ? yearOfCycle(cycle)
        : typeof extra.year === 'number'
          ? extra.year
          : num(extra.year) ?? undefined;
    const seasonName =
      cycle?.season_label != null
        ? String(cycle.season_label)
        : extra.season_label != null
          ? String(extra.season_label)
          : extra.season_type != null
            ? String(extra.season_type)
            : s?.season != null
              ? String(s.season)
              : undefined;
    const ind: WeatherIndicators | undefined = s?.weather_indicators;
    const dry = readWeatherIndicator(ind, 'max_dry_spell_days', 'dry_spell_max_days');
    const wet = readWeatherIndicator(ind, 'max_wet_spell_days', 'wet_spell_max_days');
    const heat = readWeatherIndicator(ind, 'heat_stress_days');
    const cold = readWeatherIndicator(ind, 'cold_stress_days');
    const onset = readWeatherIndicator(ind, 'monsoon_onset_offset_days', 'monsoon_onset_anomaly_days');
    const cycleId = String(extra.cycle_id ?? s?.season ?? cycle?.cycle_id ?? '');
    const riskScore =
      num(cycleRisks.find((c) => String(c.cycle_id || '') === cycleId)?.risk_score) ??
      num(cycleRisks[i]?.risk_score);
    const nested = Array.isArray(extra.extreme_events) ? extra.extreme_events : [];
    const seasonEvents =
      nested.length > 0
        ? nested
        : events.filter((ev) => {
            const evCycle = String(ev.cycle_id ?? ev.season ?? '');
            const evYear =
              num(ev.year) ?? Number(String(ev.date_or_start ?? ev.date ?? '').slice(0, 4));
            if (evCycle && cycleId && evCycle.toLowerCase() === cycleId.toLowerCase()) return true;
            if (year != null && Number.isFinite(evYear) && evYear === year) {
              const ct = String(cycle?.season_type || '').toUpperCase();
              const es = String(ev.season || '').toUpperCase();
              if (!ct || !es || es.includes(ct) || ct.includes(es)) return true;
            }
            return false;
          });
    return { s, i, cycle, dry, wet, heat, cold, onset, riskScore, seasonEvents, year, seasonName };
  }

  const usedSeasonIdx = new Set<number>();
  const rows =
    cycles.length > 0
      ? cycles.map((cycle, i) => {
          const matched = matchSeasonWeather(cycle, i, usedSeasonIdx);
          return buildWeatherRow(matched, cycle, i);
        })
      : seasonal.map((s, i) => buildWeatherRow(s, cycles[i], i));

  return (
    <div className="space-y-4">
      {/* What we found + forward / response */}
      <div className="grid lg:grid-cols-[3fr_2fr] gap-4 items-stretch">
        <div className="bg-white rounded-xl border border-rule p-5 flex flex-col">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">What we found</p>
              <h2 className="text-base font-bold text-stone-900 mt-0.5">Weather on this plot</h2>
              <p className="text-xs text-stone-500 mt-1">
                {rows.length > 0
                  ? `${rows.length} growing season${rows.length === 1 ? '' : 's'} watched`
                  : 'Across the assessed window'}
              </p>
            </div>
            <span
              className="text-[11px] font-bold px-2.5 py-1 rounded-full border shrink-0"
              style={{ color: band.ink, background: band.surface, borderColor: band.border }}
            >
              {band.word}
            </span>
          </div>

          <div className="grid sm:grid-cols-3 gap-3 mt-4 items-stretch">
            <StatTile
              label="Weather"
              value={band.word}
              hint={risk == null ? undefined : risk < 20 ? 'Manageable for the crop' : risk < 40 ? 'Some harsh spells' : 'Hard on the crop'}
              color={band.ink}
            />
            <StatTile
              label="Alerts"
              value={String(nEvents)}
              hint={typeHint}
              color={nEvents > 0 ? '#B45309' : '#15803D'}
            />
            <StatTile
              label="Crop held up"
              value={tested <= 0 || resilience == null ? 'Untested' : resilience >= 70 ? 'Well' : resilience >= 50 ? 'Fairly' : 'Poorly'}
              hint={tested <= 0 ? 'Not seen under hard weather yet' : 'How green cover held during stress'}
              color={tested <= 0 || resilience == null ? '#57534E' : resilience >= 70 ? '#006446' : resilience >= 50 ? '#7A5405' : '#9A2E1F'}
            />
          </div>

          <p className="text-[13px] text-stone-600 mt-4 leading-relaxed">{summaryParts.join(' ')}</p>

          {events.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-4 -mx-0.5">
              {Object.entries(typeCounts).map(([label, count]) => {
                const sample = events.find((ev) => prettyEventType(ev.type) === label);
                const kind = eventKind(sample?.type);
                return (
                  <span
                    key={label}
                    className="inline-flex items-center gap-1.5 text-[12px] font-medium px-2.5 py-1 rounded-full border bg-paper border-rule text-stone-700"
                  >
                    <WxIcon kind={kind} className="w-3.5 h-3.5 text-amber-800 shrink-0" />
                    {count}× {label.toLowerCase()}
                  </span>
                );
              })}
              {nCritical > 0 && (
                <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold px-2.5 py-1 rounded-full border bg-amber-50 border-amber-200 text-amber-900">
                  <WxIcon kind="alert" className="w-3.5 h-3.5 shrink-0" />
                  {nCritical} at a sensitive stage
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4 min-h-full">
          <div className="flex-1 bg-white rounded-xl border border-rule p-5 flex flex-col">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Going forward</p>
            <h2 className="text-base font-bold text-stone-900 mt-0.5">
              {exposure == null
                ? 'Weather pattern'
                : exposure >= 60
                  ? 'Higher chance of stress ahead'
                  : exposure >= 30
                    ? 'Some weather risk ahead'
                    : 'Weather risk looks contained'}
            </h2>
            <p className="text-[13px] text-stone-600 mt-2 leading-relaxed flex-1">{goingForward}</p>
          </div>
          <div className="flex-1 bg-white rounded-xl border border-rule p-5 flex flex-col">
            <div className="flex items-center gap-1.5">
              <WxIcon kind="leaf" className="w-3.5 h-3.5 text-emerald-800 shrink-0" />
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Crop response</p>
            </div>
            <h2 className="text-base font-bold text-stone-900 mt-0.5">
              {tested <= 0 || resilience == null
                ? 'Not stress-tested yet'
                : resilience >= 70
                  ? 'Held up well'
                  : resilience >= 50
                    ? 'Held up fairly'
                    : 'Struggled under stress'}
            </h2>
            <p className="text-[13px] text-stone-600 mt-2 leading-relaxed flex-1">{heldUp}</p>
          </div>
        </div>
      </div>

      {/* Each season + alerts — row height follows season cards; alerts scroll inside matched column */}
      <div className="grid lg:grid-cols-[3fr_2fr] gap-4">
        <div className="bg-white rounded-xl border border-rule p-5">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Each season</p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-3">What the weather did while the crop grew</h2>
          {rows.length === 0 ? (
            <p className="text-sm text-stone-500">No seasonal weather was stored for this plot.</p>
          ) : (
            <div className="grid sm:grid-cols-2 gap-3">
              {rows.map((row) => {
                const seasonBand = riskBand(row.riskScore);
                const dryAlert = (row.dry ?? 0) >= 21;
                const heatAlert = (row.heat ?? 0) >= 7;
                const wetAlert = (row.wet ?? 0) >= 10;
                const onsetAlert = row.onset != null && Math.abs(row.onset) >= 10;
                const notes: string[] = [];
                if (dryAlert) notes.push(`Long dry stretch — ${Math.round(row.dry!)} days without useful rain.`);
                if (heatAlert) notes.push(`Hot stretch — ${Math.round(row.heat!)} heat-stress days.`);
                if (wetAlert) notes.push(`Wet spell of ${Math.round(row.wet!)} days.`);
                if (onsetAlert && row.onset != null) {
                  notes.push(
                    row.onset > 0
                      ? `Monsoon arrived about ${Math.round(row.onset)} days later than usual.`
                      : `Monsoon arrived about ${Math.round(Math.abs(row.onset))} days earlier than usual.`
                  );
                }
                return (
                  <article key={row.i} className="rounded-lg border border-rule bg-paper/50 p-4">
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <p className="text-sm font-semibold text-stone-900">
                        {prettySeasonTitle(row.cycle, undefined, row.i)}
                      </p>
                      <span
                        className="text-[11px] font-bold px-2 py-0.5 rounded-full border shrink-0"
                        style={{
                          color: seasonBand.ink,
                          background: seasonBand.surface,
                          borderColor: seasonBand.border,
                        }}
                      >
                        {seasonBand.word}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Indicator
                        kind="dry"
                        label="Dry stretch"
                        value={daysLabel(row.dry)}
                        hint={dryAlert ? 'Longer than a typical dry spell' : 'Longest run without rain'}
                        alert={dryAlert}
                      />
                      <Indicator
                        kind="heat"
                        label="Heat"
                        value={daysLabel(row.heat)}
                        hint={heatAlert ? 'Hot enough to stress the crop' : 'Days of heat stress'}
                        alert={heatAlert}
                      />
                      <Indicator
                        kind="rain"
                        label="Wet stretch"
                        value={daysLabel(row.wet)}
                        hint="Longest run of wet days"
                        alert={wetAlert}
                      />
                      {row.cold != null && row.cold > 0 ? (
                        <Indicator kind="cold" label="Cold" value={daysLabel(row.cold)} hint="Cold-stress days" alert={row.cold >= 5} />
                      ) : onsetAlert && row.onset != null ? (
                        <Indicator
                          kind="rain"
                          label="Monsoon"
                          value={row.onset > 0 ? `${Math.round(row.onset)} d late` : `${Math.round(Math.abs(row.onset))} d early`}
                          hint="Against the usual start"
                          alert
                        />
                      ) : (
                        <Indicator
                          kind="other"
                          label="Alerts"
                          value={String(row.seasonEvents.length)}
                          hint={row.seasonEvents.length ? 'Flagged in this season' : 'None flagged'}
                          alert={row.seasonEvents.some((ev) => String(ev.severity).toLowerCase() === 'high')}
                        />
                      )}
                    </div>
                    {notes.length > 0 && (
                      <ul className="mt-3 space-y-1">
                        {notes.map((n) => (
                          <li key={n} className="flex items-start gap-1.5 text-[12px] text-stone-600 leading-snug">
                            <WxIcon kind="alert" className="w-3.5 h-3.5 text-amber-700 mt-0.5 shrink-0" />
                            {n}
                          </li>
                        ))}
                      </ul>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </div>

        {events.length > 0 ? (
          <div className="relative min-h-0 lg:self-stretch">
            <div className="bg-white rounded-xl border border-rule p-5 flex flex-col overflow-hidden lg:absolute lg:inset-0">
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold shrink-0">Alerts</p>
              <h2 className="text-sm font-bold text-stone-900 mt-0.5 shrink-0">When weather turned harsh</h2>
              <div className="flex-1 min-h-0 overflow-y-auto mt-3 space-y-2 pr-0.5 overscroll-contain">
              {events.map((ev, i) => {
                const kind = eventKind(ev.type);
                const sev = severityStyle(ev.severity);
                const when = eventDate(ev);
                const duration = num(ev.duration_days);
                return (
                  <div key={i} className="flex items-start gap-3 rounded-lg border border-rule bg-paper/50 px-3 py-2.5">
                    <span
                      className="mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-full shrink-0"
                      style={{ background: sev.surface, color: sev.ink }}
                    >
                      <WxIcon kind={kind} className="w-4 h-4" />
                    </span>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-stone-900">{prettyEventType(ev.type)}</p>
                        <span
                          className="text-[10px] font-bold px-1.5 py-0.5 rounded-full border"
                          style={{ color: sev.ink, background: sev.surface, borderColor: sev.border }}
                        >
                          {sev.word}
                        </span>
                      </div>
                      <p className="text-[12px] text-stone-500 mt-0.5">
                        {[when, duration != null ? daysLabel(duration) : null].filter(Boolean).join(' · ') ||
                          'Date not stored'}
                      </p>
                    </div>
                  </div>
                );
              })}
              </div>
            </div>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-rule p-5 flex flex-col">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Alerts</p>
            <h2 className="text-sm font-bold text-stone-900 mt-0.5">When weather turned harsh</h2>
            <p className="text-sm text-stone-500 mt-2">No extreme weather alerts were flagged for this plot.</p>
          </div>
        )}
      </div>
    </div>
  );
}
