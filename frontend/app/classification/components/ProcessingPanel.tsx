'use client';

import type { JobProgress, JobStage, ValidationCheck } from '../types';
import { STAGE_LABELS } from '../types';

const STAGE_ORDER: JobStage[] = [
  'queued',
  'validating',
  'extracting',
  'segmenting',
  'classifying',
  'vectorizing',
  'complete',
];

const STAGE_NOTES: Partial<Record<JobStage, string>> = {
  validating: 'Land-cover and cropland checks before any imagery is paid for.',
  extracting: 'Sentinel-2 composites across the season window.',
  segmenting: 'Tracing field edges from true-colour, NIR and vegetation change.',
  classifying: 'Naming a crop per field, with abstention where evidence is thin.',
  vectorizing: 'Converting the classified raster into field polygons.',
};

function CheckRow({ check }: { check: ValidationCheck }) {
  const icon = {
    pending: '○',
    running: '◐',
    pass: '✓',
    warn: '!',
    fail: '×',
  }[check.status];

  const tone = {
    pending: 'text-stone-400 border-rule',
    running: 'text-sky-700 border-sky-300 bg-sky-50',
    pass: 'text-emerald-700 border-emerald-300 bg-emerald-50',
    warn: 'text-amber-800 border-amber-300 bg-amber-50',
    fail: 'text-red-700 border-red-300 bg-red-50',
  }[check.status];

  return (
    <li className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 ${tone}`}>
      <span className="font-bold leading-5 w-4 text-center flex-shrink-0" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-5">{check.label}</p>
        {check.detail && <p className="text-xs opacity-90 mt-0.5">{check.detail}</p>}
      </div>
      {typeof check.value === 'number' && (
        <span className="text-xs font-mono flex-shrink-0 tabular-nums">
          {(check.value * 100).toFixed(0)}%
        </span>
      )}
    </li>
  );
}

export function ProcessingPanel({ progress }: { progress: JobProgress }) {
  const activeIdx = STAGE_ORDER.indexOf(progress.stage);
  const failed = progress.stage === 'failed';

  return (
    <div className="space-y-6">
      <ol className="space-y-2">
        {STAGE_ORDER.slice(0, -1).map((stage, idx) => {
          const done = !failed && activeIdx > idx;
          const active = progress.stage === stage;
          return (
            <li key={stage} className="flex items-start gap-3">
              <span
                className={`mt-0.5 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold border flex-shrink-0 ${
                  done
                    ? 'bg-emerald-500 border-emerald-500 text-white'
                    : active
                      ? 'bg-emerald-500/15 border-emerald-500 text-emerald-700 animate-pulse'
                      : 'bg-white border-rule text-stone-400'
                }`}
              >
                {done ? '✓' : idx + 1}
              </span>
              <div className="min-w-0 flex-1 pb-1">
                <p
                  className={`text-sm font-semibold ${
                    active ? 'text-stone-900' : done ? 'text-stone-600' : 'text-stone-400'
                  }`}
                >
                  {STAGE_LABELS[stage]}
                </p>
                {(active || done) && STAGE_NOTES[stage] && (
                  <p className="text-xs text-stone-500 mt-0.5">{STAGE_NOTES[stage]}</p>
                )}
                {active && progress.percent !== null && (
                  <div className="mt-2 h-1.5 rounded-full bg-stone-200 overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 transition-all duration-500"
                      style={{ width: `${Math.min(100, Math.max(0, progress.percent))}%` }}
                    />
                  </div>
                )}
                {active && progress.message && (
                  <p className="text-xs text-stone-600 mt-1.5 font-mono">{progress.message}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {progress.checks.length > 0 && (
        <section>
          <h3 className="text-sm font-bold text-stone-900 mb-2">Area validation</h3>
          <ul className="space-y-1.5">
            {progress.checks.map((c) => (
              <CheckRow key={c.id} check={c} />
            ))}
          </ul>
        </section>
      )}

      {failed && (
        <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3">
          <p className="text-sm font-semibold text-red-800 mb-1">Classification failed</p>
          <p className="text-xs text-red-700 leading-relaxed">
            {progress.error || 'The job stopped before producing a result.'}
          </p>
        </div>
      )}
    </div>
  );
}
