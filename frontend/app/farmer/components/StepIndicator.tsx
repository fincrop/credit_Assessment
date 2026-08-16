'use client';

const STEPS = [
  { id: 1, label: 'Identity' },
  { id: 2, label: 'Location' },
  { id: 3, label: 'Boundaries' },
  { id: 4, label: 'History' },
];

export function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex items-center w-full gap-1 sm:gap-2 mb-8">
      {STEPS.map((step, idx) => {
        const done = current > step.id;
        const active = current === step.id;
        return (
          <li key={step.id} className="flex items-center flex-1 min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <span
                className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border transition-colors ${
                  done
                    ? 'bg-emerald-500 border-emerald-500 text-paper'
                    : active
                      ? 'bg-emerald-500/20 border-emerald-500 text-emerald-700'
                      : 'bg-white border-rule text-ink-muted'
                }`}
              >
                {done ? '✓' : step.id}
              </span>
              <span
                className={`text-xs sm:text-sm font-medium truncate ${
                  active ? 'text-stone-900' : done ? 'text-stone-500' : 'text-ink-muted'
                }`}
              >
                {step.label}
              </span>
            </div>
            {idx < STEPS.length - 1 && (
              <div
                className={`flex-1 h-px mx-2 sm:mx-3 ${done ? 'bg-emerald-500/50' : 'bg-[#30363d]'}`}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
