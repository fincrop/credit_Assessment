'use client';

import type { TriState } from '../types/assessment';

export function TriStateSelect({
  label,
  value,
  onChange,
  compact = false,
}: {
  label: string;
  value: TriState;
  onChange: (v: TriState) => void;
  /** Match farmer journey field height (default dashboard uses larger padding). */
  compact?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-stone-600 mb-1">{label}</label>
      <select
        value={value === null ? 'unknown' : value ? 'yes' : 'no'}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === 'yes' ? true : v === 'no' ? false : null);
        }}
        className={
          compact
            ? 'w-full bg-white border border-rule rounded-lg px-3 py-2.5 text-sm text-stone-800 focus:outline-none focus:border-emerald-500'
            : 'w-full bg-paper border border-rule text-stone-800 rounded-lg p-3 text-sm focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500'
        }
      >
        <option value="unknown">Unknown</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </div>
  );
}
