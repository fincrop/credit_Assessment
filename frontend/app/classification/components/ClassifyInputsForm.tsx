'use client';

import { useMemo } from 'react';
import type { ClassificationInputs, DelineationMethod, SeasonValue } from '../types';
import { CLASSIFIABLE_CROPS, DELINEATION_METHODS, SEASONS, classColor, formatHa } from '../types';

interface Props {
  value: ClassificationInputs;
  onChange: (next: ClassificationInputs) => void;
  totalAreaHa: number;
  disabled?: boolean;
}

/**
 * Sentinel-2 revisit is 5 days and the pipeline composites to 10, so a season
 * window has roughly 12-18 usable scenes before cloud loss. Below ~5 the
 * classifier abstains, which is why the estimate is surfaced here rather than
 * discovered after the run.
 */
function estimateScenes(season: SeasonValue): number {
  return season === 'whole_year' ? 36 : 12;
}

function seasonWindow(season: SeasonValue, year: number): string {
  switch (season) {
    case 'kharif':
      return `Jun ${year} – Oct ${year}`;
    case 'rabi':
      return `Oct ${year} – Mar ${year + 1}`;
    case 'zaid':
      return `Mar ${year} – Jun ${year}`;
    default:
      return `Jun ${year} – Jun ${year + 1}`;
  }
}

export function ClassifyInputsForm({ value, onChange, totalAreaHa, disabled }: Props) {
  const set = <K extends keyof ClassificationInputs>(k: K, v: ClassificationInputs[K]) =>
    onChange({ ...value, [k]: v });

  const years = useMemo(() => {
    const now = new Date().getFullYear();
    return Array.from({ length: 6 }, (_, i) => now - i);
  }, []);

  const toggleCrop = (crop: string) => {
    const has = value.target_crops.includes(crop);
    set(
      'target_crops',
      has ? value.target_crops.filter((c) => c !== crop) : [...value.target_crops, crop]
    );
  };

  // A rough guide, not a quote. Cost scales with area because the extraction
  // is per-pixel-block, not per-field.
  const estMinutes = Math.max(2, Math.round(totalAreaHa / 120) + 3);

  return (
    <div className="space-y-6">
      <section>
        <label className="block text-sm font-bold text-stone-900 mb-1" htmlFor="cls-region">
          Region name
        </label>
        <p className="text-xs text-stone-500 mb-2">
          How this run is labelled in history, on the result, and in download filenames.
        </p>
        <input
          id="cls-region"
          type="text"
          value={value.region_name ?? ''}
          disabled={disabled}
          maxLength={80}
          placeholder="e.g. Kheda village, north block"
          onChange={(e) => set('region_name', e.target.value)}
          onBlur={() => set('region_name', value.region_name.trim())}
          className="w-full rounded-lg border border-rule bg-white px-3 py-2 text-sm text-stone-800 placeholder:text-stone-400 disabled:opacity-60"
        />
      </section>

      <section>
        <h3 className="text-sm font-bold text-stone-900 mb-1">Season</h3>
        <p className="text-xs text-stone-500 mb-3">
          Sets the observation window. The classifier looks for one crop cycle inside it.
        </p>
        <div className="grid grid-cols-2 gap-2">
          {SEASONS.map((s) => (
            <button
              key={s.value}
              type="button"
              disabled={disabled}
              onClick={() => set('season', s.value)}
              className={`text-left rounded-xl border px-3 py-2.5 transition-colors disabled:opacity-60 ${
                value.season === s.value
                  ? 'border-emerald-500 bg-emerald-50'
                  : 'border-rule bg-white/70 hover:border-emerald-300'
              }`}
            >
              <span className="block text-sm font-semibold text-stone-900">{s.label}</span>
              <span className="block text-[11px] text-stone-500">{s.hint}</span>
            </button>
          ))}
        </div>
      </section>

      <section>
        <label className="block text-sm font-bold text-stone-900 mb-1" htmlFor="cls-year">
          Agricultural year
        </label>
        <select
          id="cls-year"
          value={value.year}
          disabled={disabled}
          onChange={(e) => set('year', Number(e.target.value))}
          className="w-full rounded-lg border border-rule bg-white px-3 py-2 text-sm text-stone-800 disabled:opacity-60"
        >
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <p className="text-xs text-stone-500 mt-1.5 font-mono">
          Window: {seasonWindow(value.season, value.year)} · ~{estimateScenes(value.season)}{' '}
          composites
        </p>
      </section>

      <section>
        <h3 className="text-sm font-bold text-stone-900 mb-1">Crops to look for</h3>
        <p className="text-xs text-stone-500 mb-3">
          Leave all unselected to consider every crop the model knows. Narrowing helps only when
          you already know what grows here — it cannot add a crop the model was never trained on.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {CLASSIFIABLE_CROPS.map((crop) => {
            const on = value.target_crops.includes(crop);
            return (
              <button
                key={crop}
                type="button"
                disabled={disabled}
                onClick={() => toggleCrop(crop)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors disabled:opacity-60 ${
                  on
                    ? 'border-stone-400 bg-white text-stone-900 font-semibold'
                    : 'border-rule bg-white/50 text-stone-500 hover:border-stone-300'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: on ? classColor(crop) : '#D6D0C6' }}
                  aria-hidden
                />
                {crop}
              </button>
            );
          })}
        </div>
        {value.target_crops.length > 0 && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => set('target_crops', [])}
            className="mt-2 text-xs text-stone-500 hover:text-emerald-700 underline"
          >
            Clear ({value.target_crops.length} selected)
          </button>
        )}
      </section>

      <section className="space-y-4">
        <h3 className="text-sm font-bold text-stone-900">Output tuning</h3>

        <div>
          <label className="text-xs font-semibold text-stone-700 block mb-1" htmlFor="cls-delin">
            Field boundaries from
          </label>
          <select
            id="cls-delin"
            value={value.delineation_method}
            disabled={disabled}
            onChange={(e) => set('delineation_method', e.target.value as DelineationMethod)}
            className="w-full rounded-lg border border-rule bg-white px-3 py-2 text-sm text-stone-800"
          >
            {DELINEATION_METHODS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-stone-500 mt-1">
            {DELINEATION_METHODS.find((m) => m.value === value.delineation_method)?.hint}
          </p>
        </div>

        <div>
          <div className="flex justify-between items-baseline mb-1">
            <label className="text-xs font-semibold text-stone-700" htmlFor="cls-minarea">
              Smallest field to keep
            </label>
            <span className="text-xs font-mono text-stone-600">
              {value.min_field_area_ha.toFixed(2)} ha
            </span>
          </div>
          <input
            id="cls-minarea"
            type="range"
            min={0.05}
            max={2}
            step={0.05}
            value={value.min_field_area_ha}
            disabled={disabled}
            onChange={(e) => set('min_field_area_ha', Number(e.target.value))}
            className="w-full accent-emerald-600"
          />
          <p className="text-[11px] text-stone-500 mt-1">
            Below ~0.2 ha a field is under 20 Sentinel-2 pixels and its mean is noisy.
          </p>
        </div>

        <div>
          <div className="flex justify-between items-baseline mb-1">
            <label className="text-xs font-semibold text-stone-700" htmlFor="cls-conf">
              Confidence to name a crop
            </label>
            <span className="text-xs font-mono text-stone-600">
              {(value.confidence_threshold * 100).toFixed(0)}%
            </span>
          </div>
          <input
            id="cls-conf"
            type="range"
            min={0.15}
            max={0.7}
            step={0.05}
            value={value.confidence_threshold}
            disabled={disabled}
            onChange={(e) => set('confidence_threshold', Number(e.target.value))}
            className="w-full accent-emerald-600"
          />
          <p className="text-[11px] text-stone-500 mt-1">
            Fields below this are left <em>Abstained</em> rather than guessed. Measured sweet spot
            is 25% — stricter mostly discards fields the model had right.
          </p>
        </div>

        <label className="flex items-start gap-2.5 cursor-pointer">
          <input
            type="checkbox"
            checked={value.apply_region_guard}
            disabled={disabled}
            onChange={(e) => set('apply_region_guard', e.target.checked)}
            className="mt-0.5 accent-emerald-600"
          />
          <span className="text-xs text-stone-700 leading-relaxed">
            <span className="font-semibold text-stone-900">Region support guard</span> — abstain
            where the crop has no training data in this agro-ecoregion. Recommended: the model
            scores ~0.81 inside regions it knows and ~0.22 outside them.
          </span>
        </label>

        <label className="flex items-start gap-2.5 cursor-pointer">
          <input
            type="checkbox"
            checked={value.apply_season_mask}
            disabled={disabled}
            onChange={(e) => set('apply_season_mask', e.target.checked)}
            className="mt-0.5 accent-emerald-600"
          />
          <span className="text-xs text-stone-700 leading-relaxed">
            <span className="font-semibold text-stone-900">Season plausibility mask</span> —
            down-weight crops that do not belong in the detected cycle&apos;s season.
          </span>
        </label>
      </section>

      <div className="rounded-xl border border-rule bg-stone-50/80 px-4 py-3">
        <p className="text-xs text-stone-600">
          <span className="font-semibold text-stone-800">{formatHa(totalAreaHa)}</span> queued ·
          estimated runtime <span className="font-mono">~{estMinutes} min</span>
        </p>
      </div>
    </div>
  );
}
