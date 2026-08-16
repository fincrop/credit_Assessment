'use client';

import { useMemo, useState, useRef, useEffect } from 'react';

export interface SearchableOption {
  value: string;
  label: string;
  meta?: string;
}

interface Props {
  options: SearchableOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  allowFreeText?: boolean;
  onFreeText?: (text: string) => void;
  freeTextValue?: string;
}

export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = 'Select…',
  disabled,
  loading,
  allowFreeText,
  onFreeText,
  freeTextValue,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return options.slice(0, 80);
    return options
      .filter(
        (o) =>
          o.label.toLowerCase().includes(needle) ||
          o.value.toLowerCase().includes(needle) ||
          (o.meta && o.meta.toLowerCase().includes(needle))
      )
      .slice(0, 80);
  }, [options, q]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left bg-white border border-rule rounded-lg px-3 py-2.5 text-sm text-stone-800 focus:outline-none focus:border-emerald-500 disabled:opacity-40 flex items-center justify-between gap-2"
      >
        <span className={selected || freeTextValue ? 'text-stone-800 truncate' : 'text-ink-muted'}>
          {loading
            ? 'Loading…'
            : selected?.label || freeTextValue || placeholder}
        </span>
        <span className="text-ink-muted text-xs">▾</span>
      </button>

      {open && !disabled && (
        <div className="absolute z-30 mt-1 w-full bg-white border border-rule rounded-lg shadow-xl overflow-hidden">
          <input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              if (allowFreeText && onFreeText) onFreeText(e.target.value);
            }}
            placeholder="Type to search…"
            className="w-full bg-paper border-b border-rule px-3 py-2 text-sm text-stone-800 outline-none"
          />
          <ul className="max-h-48 overflow-y-auto">
            {filtered.length === 0 && (
              <li className="px-3 py-2 text-xs text-ink-muted">
                {allowFreeText ? 'No matches — free text will be used' : 'No matches'}
              </li>
            )}
            {filtered.map((o) => (
              <li key={o.value}>
                <button
                  type="button"
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-paper ${
                    o.value === value ? 'text-emerald-700' : 'text-stone-700'
                  }`}
                  onClick={() => {
                    onChange(o.value);
                    setQ('');
                    setOpen(false);
                  }}
                >
                  {o.label}
                  {o.meta && (
                    <span className="block text-[10px] text-ink-muted font-mono">{o.meta}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
