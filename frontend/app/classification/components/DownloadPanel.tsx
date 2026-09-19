'use client';

import { useEffect, useRef, useState } from 'react';
import type { ClassificationResult, DownloadFormat } from '../types';
import { DOWNLOAD_OPTIONS } from '../types';

interface Props {
  result: ClassificationResult;
}

/**
 * GeoJSON and CSV are produced in the browser from the result already in
 * memory. Raster products and the shapefile need the server, which holds
 * the classified raster the browser never received.
 */
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

function toCsv(result: ClassificationResult): string {
  const head = ['field_id', 'crop', 'area_ha', 'confidence', 'lon', 'lat'];
  const rows = (result.fields.features || []).map((f, i) => {
    const p = (f.properties || {}) as Record<string, unknown>;
    const c = (p.centroid as { lat?: number; lng?: number }) || {};
    return [
      String(p.field_id ?? i + 1),
      String(p.crop ?? 'Unclassified'),
      typeof p.area_ha === 'number' ? p.area_ha.toFixed(4) : '',
      typeof p.confidence === 'number' ? p.confidence.toFixed(4) : '',
      c.lng?.toFixed(6) ?? '',
      c.lat?.toFixed(6) ?? '',
    ]
      .map((v) => (v.includes(',') ? `"${v}"` : v))
      .join(',');
  });

  const summary = [
    '',
    '# Summary',
    'crop,field_count,area_ha,area_share,mean_confidence',
    ...result.stats.map((s) =>
      [s.crop, s.field_count, s.area_ha.toFixed(3), s.area_share.toFixed(4), s.mean_confidence.toFixed(4)].join(',')
    ),
  ];

  return [head.join(','), ...rows, ...summary].join('\n');
}

export function DownloadPanel({ result }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<DownloadFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const safeName = (result.aoi_name || 'classification').replace(/[^\w.-]+/g, '_');
  const stem = `${safeName}_${result.season}_${result.year}`;

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

  const handle = async (format: DownloadFormat) => {
    setError(null);
    setBusy(format);
    try {
      if (format === 'geojson') {
        triggerDownload(
          new Blob(
            [JSON.stringify({ ...result.fields, name: result.aoi_name }, null, 2)],
            { type: 'application/geo+json' }
          ),
          `${stem}.geojson`
        );
      } else if (format === 'csv') {
        triggerDownload(new Blob([toCsv(result)], { type: 'text/csv' }), `${stem}.csv`);
      } else {
        const res = await fetch(
          `/api/classification/download/${result.job_id}?format=${format}`,
          { credentials: 'include' }
        );
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
          <path
            d="M8 2v8m0 0L5 7m3 3 3-3M3 13h10"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {busy ? 'Preparing…' : 'Download'}
      </button>
      {open && (
        <div className="absolute right-0 mt-1.5 w-64 rounded-xl border border-rule bg-paper-raised shadow-overlay overflow-hidden z-[1200]">
          <p className="px-3 pt-2.5 pb-1.5 text-[10px] uppercase tracking-wide text-stone-500 font-semibold">
            Choose a format
          </p>
          {DOWNLOAD_OPTIONS.map((opt) => (
            <button
              key={opt.format}
              type="button"
              onClick={() => void handle(opt.format)}
              disabled={busy !== null}
              className="w-full text-left px-3 py-2 hover:bg-emerald-50 transition-colors disabled:opacity-60 disabled:cursor-wait"
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
        <p className="absolute right-0 mt-1 w-64 rounded-lg bg-red-50 border border-red-200 px-2 py-1 text-[11px] text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
