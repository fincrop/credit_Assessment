'use client';

import { useRef, useState, type DragEvent } from 'react';
import type { FarmPolygon } from '../types';
import { GEOSPATIAL_ACCEPT, parseGeospatialFile } from '../lib/parseGeospatial';

interface Props {
  farms: FarmPolygon[];
  onFarmsChange: (farms: FarmPolygon[]) => void;
  /** Side panel in Farm boundaries — vertical, equal height with draw hint. */
  compact?: boolean;
}

export function GeospatialUpload({ farms, onFarmsChange, compact = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      let next = [...farms];
      const notes: string[] = [];
      for (const file of Array.from(files)) {
        const imported = await parseGeospatialFile(file, next.length);
        next = [...next, ...imported];
        notes.push(`${file.name}: ${imported.length} polygon(s)`);
      }
      onFarmsChange(next);
      setMessage(`Imported — ${notes.join('; ')}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const dropHandlers = {
    onDragOver: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(true);
    },
    onDragLeave: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      void handleFiles(e.dataTransfer.files);
    },
  };

  const activeCls =
    busy || dragOver
      ? 'border-emerald-400 bg-emerald-50/50'
      : 'border-rule bg-paper-raised hover:border-emerald-400/60 hover:bg-emerald-50/30';

  if (compact) {
    return (
      <div className="h-full flex flex-col space-y-2 min-h-[6.5rem]">
        <div
          className={`flex-1 flex flex-col justify-center gap-2 border-2 border-dashed rounded-lg px-3 py-2.5 transition-colors ${activeCls}`}
          {...dropHandlers}
        >
          <p className="text-xs font-semibold text-stone-800">
            {busy ? 'Parsing…' : 'Upload geospatial file'}
          </p>
          <p className="text-[11px] text-stone-500 leading-snug">
            Drop or browse — GeoJSON, KML/KMZ, shapefile ZIP. Makes Identity &amp; Location optional.
          </p>
          <input
            ref={inputRef}
            type="file"
            accept={GEOSPATIAL_ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => void handleFiles(e.target.files)}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className="mt-1 w-full inline-flex items-center justify-center bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-300 text-white text-xs font-semibold px-3 py-2 rounded-lg"
          >
            {busy ? 'Parsing…' : 'Choose file'}
          </button>
        </div>
        {message && (
          <p className="text-[11px] text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-2 py-1.5">
            {message}
          </p>
        )}
        {error && (
          <p className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div
        className={`w-full flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-2 border-dashed rounded-lg px-4 py-3 transition-colors ${activeCls}`}
        {...dropHandlers}
      >
        <div className="min-w-0 flex-1 text-left">
          <p className="text-sm font-medium text-stone-800">
            {busy ? 'Parsing file…' : 'Drop file here or choose to browse'}
          </p>
          <p className="text-xs text-stone-500 mt-0.5 leading-relaxed">
            GeoJSON, KML/KMZ, or shapefile ZIP (.shp + .shx + .dbf). For GeoPackage (.gpkg),
            export to GeoJSON or shapefile ZIP first. Polygons appear on the map above.
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={GEOSPATIAL_ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className="shrink-0 inline-flex items-center justify-center bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-300 text-white text-sm font-semibold px-4 py-2.5 rounded-lg w-full sm:w-auto"
        >
          {busy ? 'Parsing…' : 'Choose file'}
        </button>
      </div>

      {message && (
        <p className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
          {message}
        </p>
      )}
      {error && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {error}
        </p>
      )}
    </div>
  );
}
