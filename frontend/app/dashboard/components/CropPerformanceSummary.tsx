'use client';

import { resolveCropCycles } from '../../lib/ndviCycles';
import { prettySeasonTitle } from '../../lib/seasonLabels';
import type {
  AssessmentPayload,
  CropCycle,
  CropVerification,
  SeasonPerformance,
} from '../../types/assessment';
import type { ReportNdviTrajectory } from '../../types/report';

function isNamedCrop(name: string | null | undefined): boolean {
  if (!name) return false;
  const n = name.trim().toLowerCase();
  return n !== '' && n !== 'unknown' && n !== 'unclassified' && n !== 'none' && n !== 'none declared';
}

function prettyCrop(name: string | null | undefined): string {
  return isNamedCrop(name) ? String(name) : 'Not identified';
}

function prettyDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function prettyPattern(raw: string | null | undefined): string {
  const key = String(raw || '').toUpperCase();
  if (key.includes('DOUBLE')) return 'Two crops a year';
  if (key.includes('CONTINUOUS') || key.includes('YEAR_ROUND')) return 'Cropped through the year';
  if (key.includes('MULTI_YEAR') || key.includes('SINGLE')) return 'One crop, year after year';
  if (!raw) return '—';
  return String(raw).replace(/_/g, ' ').toLowerCase();
}

function growthBand(score: number | null | undefined): {
  word: string;
  why: string;
  ink: string;
  surface: string;
  border: string;
} {
  if (score == null || !Number.isFinite(score)) {
    return {
      word: 'Unknown',
      why: 'No growth score was stored for this season.',
      ink: '#57534E',
      surface: '#F0EDE6',
      border: '#E4DFD4',
    };
  }
  if (score >= 70) {
    return {
      word: 'Strong',
      why: 'Green cover stayed healthy through most of the season.',
      ink: '#006446',
      surface: '#DFF0E9',
      border: '#C4E2D6',
    };
  }
  if (score >= 50) {
    return {
      word: 'Fair',
      why: 'Growth was mixed — some healthy stretches and some weaker ones.',
      ink: '#7A5405',
      surface: '#FCF0D9',
      border: '#F5E2B8',
    };
  }
  return {
    word: 'Weak',
    why: 'Green cover was low for much of the season.',
    ink: '#9A2E1F',
    surface: '#FAE8E4',
    border: '#F2D2CB',
  };
}

function cropCheckCopy(
  v: CropVerification | null | undefined,
  nCycles: number,
  observedCrop: string | null,
  pattern: string
): { title: string; body: string } {
  if (!v || !isNamedCrop(v.declared_crop)) {
    if (isNamedCrop(observedCrop)) {
      return {
        title: `Growth looks like ${observedCrop}`,
        body:
          nCycles > 0
            ? `No crop was declared on the land record. From the vegetation pattern we read ${nCycles} growing season${nCycles === 1 ? '' : 's'} that resemble ${observedCrop}. That is an observation, not a confirmation of what was sown.`
            : `No crop was declared. The canopy pattern resembles ${observedCrop}.`,
      };
    }
    if (nCycles > 0) {
      return {
        title: 'Crop was not declared — seasons were still measured',
        body: `The land record does not name a crop, so we cannot check a declaration. We still found ${nCycles} growing season${nCycles === 1 ? '' : 's'} on this plot${pattern && pattern !== '—' ? ` (${pattern.toLowerCase()})` : ''}. Naming the crop on the next assessment lets us check whether growth matches what was claimed.`,
      };
    }
    return {
      title: 'Crop was not declared',
      body: 'The land record does not name a crop. We judged this plot from its own green-cover pattern rather than against a named crop calendar.',
    };
  }
  const named = v.declared_crop as string;
  const outcome = String(v.outcome || 'indeterminate');
  if (outcome === 'consistent') {
    return {
      title: 'Matches the named crop',
      body: v.reason || `Growth looks like ${named}, which is what was declared.`,
    };
  }
  if (outcome === 'inconsistent') {
    return {
      title: 'Does not match the named crop',
      body:
        v.reason ||
        `The farmer named ${named}, but the growth pattern looks different. This is often a data-entry slip or a mid-season change — worth asking.`,
    };
  }
  return {
    title: 'Cannot confirm the named crop',
    body: v.reason || `A crop was named (${named}), but the signal is not clear enough to confirm it.`,
  };
}

function yearOf(cycle: CropCycle): number | null {
  const fromLabel = String(cycle.season_label || '').match(/20\d{2}/)?.[0];
  const fromSos = cycle.phenology?.sos?.slice(0, 4);
  const fromSow = cycle.sowing_date?.slice(0, 4);
  const y = Number(fromLabel || fromSos || fromSow);
  return Number.isFinite(y) ? y : null;
}

function matchPerformance(cycle: CropCycle, seasons: SeasonPerformance[], index: number): SeasonPerformance | undefined {
  const year = yearOf(cycle);
  const label = `${cycle.season_label || ''} ${cycle.season_type || ''}`.toUpperCase();
  const hit = seasons.find((s) => {
    const season = String(s.season || '').toUpperCase();
    const yearOk = year == null || s.year == null || s.year === year;
    if (!yearOk) return false;
    if (label.includes('KHARIF') && season.includes('KHARIF')) return true;
    if (label.includes('RABI') && season.includes('RABI')) return true;
    if (year != null && s.year === year) return true;
    return false;
  });
  return hit || seasons[index];
}

function landUseCopy(lui: number | null, cropsPerYear: number | null, nCycles: number): string {
  const parts: string[] = [];
  if (nCycles === 1) parts.push('One growing season was found.');
  if (nCycles > 1) parts.push(`${nCycles} growing seasons were found.`);
  if (lui != null) {
    if (lui < 0.35) parts.push('The land was in crop for only a small part of the year.');
    else if (lui < 0.65) parts.push('The land was in crop for about half the year.');
    else parts.push('The land was in crop for most of the year.');
  }
  if (cropsPerYear != null) {
    if (cropsPerYear < 0.8) parts.push('That is less than one harvest a year on average.');
    else if (cropsPerYear < 1.4) parts.push('That is about one crop a year.');
    else parts.push('That is more than one crop a year.');
  }
  return parts.join(' ');
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
      {hint && <p className="text-[11px] text-ink-muted mt-1.5 leading-snug">{hint}</p>}
    </div>
  );
}

export function CropPerformanceSummary({
  data,
  ndviTrajectory,
}: {
  data: AssessmentPayload;
  ndviTrajectory?: ReportNdviTrajectory | null;
}) {
  const ca = data.cropping_analysis;
  const pa = data.performance_analysis;
  const cc = data.crop_cycles;
  const stats = data.continuous_data_stats;
  const verification = ca?.crop_verification;
  const seasons: SeasonPerformance[] = pa?.seasonal_performance ?? [];
  const cycles: CropCycle[] = resolveCropCycles(cc?.cycles, ndviTrajectory);
  const nCycles = cycles.length || seasons.length || cc?.cycles_count || pa?.n_complete_cycles || 0;
  const um = cc?.utilization_metrics ?? {};
  const lui = um.land_utilization_index;
  const lui01 = lui == null ? null : lui > 1 ? lui / 100 : lui;
  const spanDays =
    stats?.date_range?.start && stats?.date_range?.end
      ? Math.max(
          1,
          Math.round(
            (new Date(stats.date_range.end).getTime() - new Date(stats.date_range.start).getTime()) /
              86400000
          )
        )
      : null;
  const croppedDays = cycles.reduce((acc, c) => acc + (Number(c.duration_days) || 0), 0);
  const inferredLui =
    spanDays && croppedDays > 0 && (lui01 == null || (lui01 === 0 && cycles.length > 0))
      ? Math.min(1, croppedDays / spanDays)
      : lui01;
  const inferredLuiDisplay = inferredLui == null ? '—' : `${(inferredLui * 100).toFixed(1)}%`;
  const storedCpy = um.crops_per_year ?? ca?.cropping_intensity ?? null;
  const inferredCpy = spanDays && cycles.length ? cycles.length / (spanDays / 365.25) : null;
  const cropsPerYear =
    storedCpy == null || (storedCpy === 0 && cycles.length > 0) ? inferredCpy : storedCpy;
  const health = pa?.average_health_score;
  const overall = growthBand(health);
  const observedCrop = ca?.dominant_crop || seasons.find((s) => isNamedCrop(s.crop))?.crop || null;
  const storedPattern = prettyPattern(um.cropping_pattern);
  const inferredPattern =
    cropsPerYear == null
      ? '—'
      : cropsPerYear >= 1.6
        ? 'Two crops a year'
        : cropsPerYear >= 0.8
          ? 'One crop, year after year'
          : 'Less than one crop a year';
  const patternLabel =
    (cc?.cycles?.length ?? 0) >= cycles.length && storedPattern !== '—'
      ? storedPattern
      : inferredPattern;
  const check = cropCheckCopy(verification, nCycles, observedCrop, patternLabel);
  const peerWarm = (pa?.peer_benchmarking?.n_cycles_peer_scored ?? 0) > 0;
  const peerN = pa?.peer_benchmarking?.n_cycles_peer_scored ?? 0;
  const window =
    stats?.date_range?.start && stats?.date_range?.end
      ? `${prettyDate(stats.date_range.start)} – ${prettyDate(stats.date_range.end)}`
      : null;

  const rows =
    cycles.length > 0
      ? cycles.map((cycle, i) => ({ cycle, season: matchPerformance(cycle, seasons, i), i }))
      : seasons.map((season, i) => ({ cycle: undefined, season, i }));

  const hasAnything = !!(ca || pa || cc || rows.length);
  if (!hasAnything) {
    return (
      <div className="bg-white rounded-xl border border-rule p-6">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
          Crops &amp; seasons
        </p>
        <p className="text-sm text-stone-500 mt-2">
          No crop or season findings are stored for this plot yet.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* What we found + crop check — same row as Weather tab */}
      <div className="grid lg:grid-cols-[3fr_2fr] gap-4 items-stretch">
        <div className="bg-white rounded-xl border border-rule p-5 flex flex-col">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
                What we found
              </p>
              <h2 className="text-base font-bold text-stone-900 mt-0.5">
                Growing seasons on this plot
              </h2>
              {window && (
                <p className="text-xs text-stone-500 mt-1">Watched {window}</p>
              )}
            </div>
            <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full border shrink-0 bg-emerald-50 border-emerald-200 text-emerald-800">
              {nCycles} season{nCycles === 1 ? '' : 's'}
            </span>
          </div>

          <div className="grid sm:grid-cols-3 gap-3 mt-4 items-stretch">
            <StatTile
              label="Land in use"
              value={inferredLuiDisplay}
              hint={
                inferredLui == null
                  ? undefined
                  : inferredLui < 0.35
                    ? 'Cropped for a small part of the year'
                    : inferredLui < 0.65
                      ? 'Cropped for about half the year'
                      : 'Cropped for most of the year'
              }
              color="#15803D"
            />
            <StatTile
              label="Crops / year"
              value={cropsPerYear != null ? cropsPerYear.toFixed(1) : '—'}
              hint={patternLabel === '—' ? undefined : patternLabel}
              color="#B45309"
            />
            <StatTile
              label="Growth"
              value={overall.word}
              hint={prettyCrop(ca?.dominant_crop || seasons.find((s) => isNamedCrop(s.crop))?.crop)}
              color={overall.ink}
            />
          </div>

          <p className="text-[13px] text-stone-600 mt-4 leading-relaxed">
            {landUseCopy(inferredLui, cropsPerYear, nCycles)}{' '}
            {health != null
              ? `Overall, growth looked ${overall.word.toLowerCase()}. ${overall.why}`
              : ''}
          </p>
        </div>

        <div className="bg-white rounded-xl border border-rule p-5 flex flex-col min-h-full">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
            Crop check
          </p>
          <h2 className="text-base font-bold text-stone-900 mt-0.5">{check.title}</h2>
          {isNamedCrop(verification?.declared_crop) && (
            <p className="text-xs text-stone-500 mt-1">Declared: {verification?.declared_crop}</p>
          )}
          <p className="text-[13px] text-stone-600 mt-2 leading-relaxed flex-1">{check.body}</p>
        </div>
      </div>

      <div className="grid lg:grid-cols-[3fr_2fr] gap-4 items-start">
        <div className="bg-white rounded-xl border border-rule p-5">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
            Each season
          </p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-3">When it grew, and how it looked</h2>
          {rows.length === 0 ? (
            <p className="text-sm text-stone-500">No seasons were stored for this plot.</p>
          ) : (
            <div className="grid sm:grid-cols-2 gap-3">
              {rows.map(({ cycle, season, i }) => {
                const band = growthBand(season?.health_score);
                const ph = cycle?.phenology;
                const start = prettyDate(ph?.sos || cycle?.sowing_date);
                const peak = prettyDate(ph?.pos || (typeof cycle?.peak_date === 'string' ? cycle.peak_date : null));
                const end = prettyDate(ph?.eos || cycle?.harvest_date);
                const highStress = (season?.anomaly_events ?? []).some(
                  (e) => String(e.impact || '').toUpperCase() === 'HIGH'
                );
                const why =
                  season?.performance_narrative?.trim() ||
                  (highStress ? `${band.why} Stress was also seen this season.` : band.why);
                return (
                  <article key={i} className="rounded-lg border border-rule bg-paper/50 p-4">
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <p className="text-sm font-semibold text-stone-900">
                        {prettySeasonTitle(cycle, season, i)}
                      </p>
                      <span
                        className="text-[11px] font-bold px-2 py-0.5 rounded-full border shrink-0"
                        style={{
                          color: band.ink,
                          background: band.surface,
                          borderColor: band.border,
                        }}
                      >
                        {band.word}
                      </span>
                    </div>
                    <p className="text-[12px] text-stone-600 mb-3">
                      Crop: {prettyCrop(season?.crop)}
                      {typeof cycle?.peak_ndvi === 'number'
                        ? ` · peak NDVI ${cycle.peak_ndvi.toFixed(2)}`
                        : ''}
                    </p>
                    {(start || peak || end) && (
                      <div className="grid grid-cols-3 gap-2 text-xs text-stone-700 mb-2">
                        <div>
                          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Started</p>
                          <p className="font-medium">{start || '—'}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Peak</p>
                          <p className="font-medium">{peak || '—'}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-ink-muted uppercase tracking-wider">Ended</p>
                          <p className="font-medium">{end || '—'}</p>
                        </div>
                      </div>
                    )}
                    <p className="text-[12px] text-stone-500 leading-snug">{why}</p>
                  </article>
                );
              })}
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-rule p-5">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
            Nearby farms
          </p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5">
            {peerWarm ? 'Compared with similar farms' : 'Judged on this plot’s own growth'}
          </h2>
          <p className="text-[13px] text-stone-600 mt-2 leading-relaxed">
            {peerWarm
              ? `${peerN} season${peerN === 1 ? '' : 's'} were also checked against other farms in the same climate zone.`
              : `Not enough neighbouring farms in this area have been assessed yet, so this plot is scored from its own canopy — how green it grew, how long each season lasted, and how steadily. Neighbour comparison will appear here once more farms nearby are assessed.`}
          </p>
          {!peerWarm && health != null && (
            <p className="text-[12px] text-stone-500 mt-2 leading-snug">
              On its own terms, growth looked {overall.word.toLowerCase()}. {overall.why}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
