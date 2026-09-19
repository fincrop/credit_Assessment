'use client';

import { useEffect, useRef, useState } from 'react';
import type { ClassificationHistoryItem, JobStage } from '../types';
import { STAGE_LABELS, classColor, formatHa, seasonLabel } from '../types';

interface ListProps {
  items: ClassificationHistoryItem[];
  loading?: boolean;
  activeJobId?: string | null;
  openingId?: string | null;
  onOpen: (item: ClassificationHistoryItem) => void;
  /** Tighter rows for the header dropdown. */
  compact?: boolean;
}

function stageTone(stage: JobStage): string {
  if (stage === 'complete') return 'bg-emerald-50 text-emerald-800 border-emerald-200';
  if (stage === 'failed') return 'bg-red-50 text-red-800 border-red-200';
  return 'bg-amber-50 text-amber-900 border-amber-200';
}

function when(item: ClassificationHistoryItem): string {
  const raw = item.finished_at || item.created_at;
  if (!raw) return '';
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function Composition({ stats }: { stats: ClassificationHistoryItem['stats'] }) {
  const shown = (stats || []).filter((s) => s.area_share > 0.02).slice(0, 8);
  if (shown.length === 0) return null;
  return (
    <div className="flex h-1.5 w-full rounded-full overflow-hidden border border-rule mt-2">
      {shown.map((s) => (
        <div
          key={s.crop}
          style={{ width: `${s.area_share * 100}%`, background: classColor(s.crop) }}
          title={`${s.crop} — ${formatHa(s.area_ha)}`}
        />
      ))}
    </div>
  );
}

export function HistoryPanel({
  items,
  loading = false,
  activeJobId = null,
  openingId = null,
  onOpen,
  compact = false,
}: ListProps) {
  if (loading && items.length === 0) {
    return <p className="text-xs text-stone-500 py-2">Loading past runs…</p>;
  }
  if (items.length === 0) {
    return (
      <p className="text-xs text-stone-500 leading-relaxed">
        Previous runs for this account will show up here.
      </p>
    );
  }

  return (
    <ul className={compact ? 'space-y-1' : 'space-y-2'}>
      {items.map((item) => {
        const active = item.job_id === activeJobId;
        const opening = item.job_id === openingId;
        const top = item.stats?.[0];
        return (
          <li key={item.job_id}>
            <button
              type="button"
              onClick={() => onOpen(item)}
              disabled={opening}
              className={`w-full text-left rounded-xl border px-3 py-2.5 transition-colors disabled:opacity-60 ${
                active
                  ? 'border-emerald-400 bg-emerald-50'
                  : 'border-rule bg-white/80 hover:border-emerald-300 hover:bg-emerald-50/60'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <p className={`font-semibold text-stone-900 truncate ${compact ? 'text-xs' : 'text-sm'}`}>
                  {item.aoi_name}
                </p>
                <span
                  className={`flex-shrink-0 text-[10px] font-semibold uppercase tracking-wide border rounded-full px-1.5 py-0.5 ${stageTone(item.stage)}`}
                >
                  {opening ? 'Opening…' : STAGE_LABELS[item.stage] || item.stage}
                </span>
              </div>
              <p className="text-[11px] text-stone-500 mt-0.5">
                {seasonLabel(item.season)} {item.year}
                {' · '}
                {formatHa(item.classified_area_ha ?? item.total_area_ha)}
                {typeof item.field_count === 'number' ? ` · ${item.field_count} fields` : ''}
                {top?.crop ? ` · ${top.crop}` : ''}
              </p>
              <p className="text-[10px] text-stone-400 mt-0.5 tabular-nums">{when(item)}</p>
              {item.stage === 'failed' && item.error && (
                <p className="text-[11px] text-red-700 mt-1 line-clamp-2 leading-snug">{item.error}</p>
              )}
              {item.stage === 'complete' && <Composition stats={item.stats} />}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

interface MenuProps {
  items: ClassificationHistoryItem[];
  loading?: boolean;
  activeJobId?: string | null;
  openingId?: string | null;
  onOpen: (item: ClassificationHistoryItem) => void;
}

export function HistoryMenu({ items, loading, activeJobId, openingId, onOpen }: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!loading && items.length === 0) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-lg bg-white/95 border border-rule px-3 py-1.5 text-sm font-semibold text-stone-800 hover:border-emerald-500 hover:bg-emerald-50 transition-colors"
      >
        History
        {items.length > 0 && (
          <span className="text-[10px] font-mono text-stone-500 tabular-nums">{items.length}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 mt-1.5 w-80 max-h-[min(70vh,28rem)] overflow-y-auto rounded-xl border border-rule bg-paper-raised shadow-overlay p-2 z-[1200]">
          <p className="px-1.5 pt-1 pb-2 text-[10px] uppercase tracking-wide text-stone-500 font-semibold">
            Past classifications
          </p>
          <HistoryPanel
            items={items}
            loading={loading}
            activeJobId={activeJobId}
            openingId={openingId}
            compact
            onOpen={(item) => {
              setOpen(false);
              onOpen(item);
            }}
          />
        </div>
      )}
    </div>
  );
}
