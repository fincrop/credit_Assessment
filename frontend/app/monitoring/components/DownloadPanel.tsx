'use client';

import { useEffect, useRef, useState } from 'react';
import type { MonitorDownload, MonitoringResult } from '../types';
import { DOWNLOAD_OPTIONS } from '../types';

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function csvCell(value: unknown): string {
  const text = value == null ? '' : String(value);
  return text.includes(',') || text.includes('"') ? `"${text.replace(/"/g, '""')}"` : text;
}

function toCsv(result: MonitoringResult): string {
  const farms = result.farms || [];
  const zones = [
    farms.length
      ? 'field_id,crop,area_ha,sowing_date,harvest_date,yield_t_ha,stress'
      : 'zone_id,crop,kind,area_share,greenup,sowing_date,sowing_early,sowing_late,sowing_confidence,sowing_sources,stage,tau,harvest,yield_t_ha,yield_low,yield_high,reference_pool',
    ...(farms.length
      ? farms.map((farm) =>
          [farm.field_id, farm.crop, farm.area_ha, farm.sowing_date, farm.harvest_date, farm.yield_t_ha, farm.stress].map(csvCell).join(',')
        )
      : (result.zones || [])
          .filter((z) => z.kind !== 'non_crop')
          .map((z) =>
            [
              z.zone_id,
              z.crop,
              z.kind,
              z.area_share,
              z.greenup,
              z.sowing?.date,
              z.sowing?.early,
              z.sowing?.late,
              z.sowing?.confidence,
              (z.sowing?.sources || []).join(';'),
              z.progress?.stage,
              z.progress?.tau,
              z.progress?.harvest,
              z.yield?.t_ha,
              z.yield?.low,
              z.yield?.high,
              z.yield?.reference_pool,
            ].map(csvCell).join(',')
          )),
  ];
  const intervals = [
    '',
    '# Intervals',
    'zone_id,date,kind,stage,tau,cover,water,biomass_kg_ha,uncertainty,stress,stressed_fraction,nitrogen_score,nitrogen_band',
    ...(result.zones || []).flatMap((z) =>
      (z.intervals || []).map((item) =>
        [
          z.zone_id,
          item.date,
          item.kind,
          item.stage,
          item.tau,
          item.cover,
          item.water,
          item.biomass_kg_ha,
          item.uncertainty,
          item.stress?.type,
          item.stress?.stressed_fraction,
          item.nitrogen?.score,
          item.nitrogen?.band,
        ].map(csvCell).join(',')
      )
    ),
  ];
  return [...zones, ...intervals].join('\n');
}

export function DownloadPanel({ result }: { result: MonitoringResult }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<MonitorDownload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const stem = `${(result.name || result.crop || 'monitoring').replace(/[^\w.-]+/g, '_')}_${result.season || 'season'}`;

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

  const handle = async (format: MonitorDownload) => {
    setError(null);
    setBusy(format);
    try {
      if (format === 'geojson') {
        triggerDownload(
          new Blob([JSON.stringify(result.fields, null, 2)], { type: 'application/geo+json' }),
          `${stem}.geojson`
        );
      } else if (format === 'csv') {
        triggerDownload(new Blob([toCsv(result)], { type: 'text/csv' }), `${stem}_analysis.csv`);
      } else if (format === 'json') {
        triggerDownload(
          new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }),
          `${stem}_analysis.json`
        );
      } else {
        const res = await fetch(`/api/monitoring/download/${result.job_id}?format=${format}`, {
          credentials: 'include',
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `Server returned ${res.status}`);
        }
        const ext = format === 'shapefile' ? 'zip' : format === 'geotiff' ? 'tif' : 'png';
        triggerDownload(await res.blob(), `${stem}.${ext}`);
      }
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Download failed.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy !== null}
        className="inline-flex items-center gap-2 rounded-lg bg-white/95 border border-rule shadow-raised px-3 py-2 text-sm font-semibold text-stone-800 hover:border-emerald-500 hover:bg-emerald-50 transition-colors disabled:opacity-60"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path d="M8 2v8m0 0L5 7m3 3 3-3M3 13h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {busy ? 'Preparing…' : 'Download'}
      </button>
      {open && (
        <div className="absolute right-0 mt-1.5 w-72 rounded-xl border border-rule bg-paper-raised shadow-overlay overflow-hidden z-[1200]">
          <p className="px-3 pt-2.5 pb-1.5 text-[10px] uppercase tracking-wide text-stone-500 font-semibold">
            Maps and analytical data
          </p>
          {DOWNLOAD_OPTIONS.map((opt) => (
            <button
              key={opt.format}
              type="button"
              onClick={() => void handle(opt.format)}
              disabled={busy !== null}
              className="w-full text-left px-3 py-2 hover:bg-emerald-50 transition-colors disabled:opacity-60"
            >
              <span className="block text-sm font-semibold text-stone-900">
                {busy === opt.format ? 'Preparing…' : opt.label}
              </span>
              <span className="block text-[11px] text-stone-500">{opt.hint}</span>
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="absolute right-0 mt-1 w-72 rounded-lg bg-red-50 border border-red-200 px-2 py-1 text-[11px] text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
