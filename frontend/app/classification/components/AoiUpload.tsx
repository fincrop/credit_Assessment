'use client';

import { useRef, useState, type DragEvent } from 'react';
import type { AreaOfInterest } from '../types';
import { formatHa } from '../types';
import { AOI_ACCEPT, parseAoiFile, type AoiSplitMode } from '../lib/parseAoi';

interface Props {
  areas: AreaOfInterest[];
  onAreasChange: (areas: AreaOfInterest[]) => void;
  disabled?: boolean;
}

export function AoiUpload({ areas, onAreasChange, disabled = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [mode, setMode] = useState<AoiSplitMode>('merge');

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length || disabled) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      let next = [...areas];
      const notes: string[] = [];
      for (const file of Array.from(files)) {
        const parsed = await parseAoiFile(file, { mode, existingCount: next.length });
        next = [...next, ...parsed];
        notes.push(`${file.name} → ${parsed.length} area(s)`);
      }
      onAreasChange(next);
      setMessage(notes.join('; '));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that file.');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const stop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          stop(e);
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={(e) => {
          stop(e);
          setDragOver(false);
        }}
        onDrop={(e) => {
          stop(e);
          setDragOver(false);
          void handleFiles(e.dataTransfer.files);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        className={`rounded-xl border-2 border-dashed px-5 py-7 text-center transition-colors ${
          disabled
            ? 'border-rule bg-stone-100/60 cursor-not-allowed opacity-60'
            : dragOver
              ? 'border-emerald-400 bg-emerald-50 cursor-pointer'
              : 'border-rule bg-white/60 hover:border-emerald-300 cursor-pointer'
        }`}
      >
        <p className="text-sm font-semibold text-stone-800 mb-1">
          {busy ? 'Reading boundary…' : 'Upload a boundary file'}
        </p>
        <p className="text-xs text-stone-500 leading-relaxed">
          Village, block or area-of-interest outline.
          <br />
          GeoJSON · KML / KMZ · Shapefile ZIP
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={AOI_ACCEPT}
          multiple
          className="hidden"
          disabled={disabled}
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </div>

      <fieldset className="flex items-center gap-4 text-xs text-stone-600">
        <legend className="sr-only">How to treat multiple polygons in a file</legend>
        {(
          [
            ['merge', 'Merge into one area', 'One village split across polygons'],
            ['separate', 'Keep separate', 'File holds several distinct areas'],
          ] as const
        ).map(([val, label, hint]) => (
          <label key={val} className="flex items-center gap-1.5 cursor-pointer" title={hint}>
            <input
              type="radio"
              name="aoi-split"
              checked={mode === val}
              disabled={disabled}
              onChange={() => setMode(val)}
              className="accent-emerald-600"
            />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>

      {message && <p className="text-xs text-emerald-700">{message}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}

      {areas.length > 0 && (
        <ul className="space-y-1.5">
          {areas.map((a) => (
            <li
              key={a.aoi_id}
              className="flex items-center gap-2 rounded-lg border border-rule bg-white/70 px-3 py-2"
            >
              <span
                className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                style={{ background: a.color }}
                aria-hidden
              />
              <span className="text-sm text-stone-800 truncate flex-1">{a.name}</span>
              <span className="text-xs text-stone-500 font-mono flex-shrink-0">
                {formatHa(a.area_ha)}
              </span>
              <span className="text-[10px] uppercase tracking-wide text-stone-400 flex-shrink-0">
                {a.source}
              </span>
              {!disabled && (
                <button
                  onClick={() => onAreasChange(areas.filter((x) => x.aoi_id !== a.aoi_id))}
                  className="text-stone-400 hover:text-red-600 text-sm leading-none px-1"
                  aria-label={`Remove ${a.name}`}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
