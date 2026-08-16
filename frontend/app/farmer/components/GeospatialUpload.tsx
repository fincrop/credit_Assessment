'use client';

import { useRef, useState } from 'react';
import type { FarmPolygon } from '../types';
import { GEOSPATIAL_ACCEPT, parseGeospatialFile } from '../lib/parseGeospatial';

interface Props {
  farms: FarmPolygon[];
  onFarmsChange: (farms: FarmPolygon[]) => void;
}

export function GeospatialUpload({ farms, onFarmsChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="space-y-4">
      <div
        className={`border-2 border-dashed rounded-xl px-6 py-10 text-center transition-colors ${
          busy
            ? 'border-emerald-400 bg-emerald-50/50'
            : 'border-rule bg-paper-raised hover:border-emerald-400/60 hover:bg-emerald-50/30'
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          void handleFiles(e.dataTransfer.files);
        }}
      >
        <p className="text-sm font-semibold text-stone-800 mb-1">
          Upload farm boundaries
        </p>
        <p className="text-xs text-stone-500 mb-4 max-w-md mx-auto leading-relaxed">
          Drop a GeoJSON, KML/KMZ, or shapefile ZIP (.shp + .shx + .dbf). For GeoPackage
          (.gpkg), export to GeoJSON or shapefile ZIP first. Polygons are added to your
          farm list and shown on the map.
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
          className="inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-stone-300 text-white text-sm font-semibold px-4 py-2.5 rounded-lg"
        >
          {busy ? 'Parsing…' : 'Choose file'}
        </button>
      </div>

      {message && (
        <p className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-2.5">
          {message}
        </p>
      )}
      {error && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-2.5">
          {error}
        </p>
      )}
    </div>
  );
}
