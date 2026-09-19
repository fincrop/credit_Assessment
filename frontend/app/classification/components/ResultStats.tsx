'use client';

import type { ClassificationResult } from '../types';
import { classColor, formatHa } from '../types';

function Tile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-rule bg-white/80 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-stone-500 mb-0.5">{label}</p>
      <p className="text-base font-bold text-stone-900 tabular-nums leading-tight">{value}</p>
      {sub && <p className="text-[10px] text-stone-500 mt-0.5 leading-snug">{sub}</p>}
    </div>
  );
}

function CompositionBar({ result }: { result: ClassificationResult }) {
  const shown = result.stats.filter((s) => s.area_share > 0.002);
  return (
    <div>
      <div className="flex h-3.5 w-full rounded-md overflow-hidden border border-rule">
        {shown.map((s) => (
          <div
            key={s.crop}
            style={{ width: `${s.area_share * 100}%`, background: classColor(s.crop) }}
            title={`${s.crop} — ${formatHa(s.area_ha)} (${(s.area_share * 100).toFixed(1)}%)`}
          />
        ))}
      </div>
    </div>
  );
}

export function ResultStats({ result }: { result: ClassificationResult }) {
  const coverage =
    result.total_area_ha > 0 ? result.classified_area_ha / result.total_area_ha : 0;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-bold text-stone-900">
          {result.aoi_name}
        </h2>
        <p className="text-[11px] text-stone-500 capitalize">
          {result.season} {result.year}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Tile
          label="Area classified"
          value={formatHa(result.classified_area_ha)}
          sub={`of ${formatHa(result.total_area_ha)}`}
        />
        <Tile
          label="Fields found"
          value={result.field_count.toLocaleString()}
          sub="after merge"
        />
        <Tile
          label="Coverage"
          value={`${(coverage * 100).toFixed(0)}%`}
          sub={`${formatHa(result.unclassified_area_ha)} not named`}
        />
        <Tile
          label="Mean confidence"
          value={`${(result.mean_confidence * 100).toFixed(0)}%`}
          sub="named fields"
        />
      </div>

      {result.region_support && !result.region_support.supported && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2">
          <p className="text-xs font-semibold text-amber-900 mb-0.5">
            Outside trained regions
          </p>
          <p className="text-[11px] text-amber-800 leading-relaxed">
            This area sits in <span className="font-mono">{result.region_support.ecoregion}</span>
            {result.region_support.unsupported_crops.length > 0 && (
              <> — limited data for {result.region_support.unsupported_crops.join(', ')}</>
            )}
            . Treat labels as indicative.
          </p>
        </div>
      )}

      <section>
        <h3 className="text-xs font-bold text-stone-900 mb-2">Crop composition</h3>
        <CompositionBar result={result} />
      </section>

      <section>
        <h3 className="text-xs font-bold text-stone-900 mb-2">Per-class statistics</h3>
        <div className="overflow-x-auto rounded-lg border border-rule">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-stone-50 text-left">
                <th className="px-2.5 py-1.5 font-semibold text-stone-700">Crop</th>
                <th className="px-2.5 py-1.5 font-semibold text-stone-700 text-right">Fields</th>
                <th className="px-2.5 py-1.5 font-semibold text-stone-700 text-right">Area</th>
                <th className="px-2.5 py-1.5 font-semibold text-stone-700 text-right">Share</th>
                <th className="px-2.5 py-1.5 font-semibold text-stone-700 text-right">Conf.</th>
              </tr>
            </thead>
            <tbody>
              {result.stats.map((s) => (
                <tr key={s.crop} className="border-t border-rule">
                  <td className="px-2.5 py-1.5">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="w-2 h-2 rounded-sm flex-shrink-0"
                        style={{ background: classColor(s.crop) }}
                        aria-hidden
                      />
                      <span className="text-stone-800">{s.crop}</span>
                    </span>
                  </td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums text-stone-700">
                    {s.field_count.toLocaleString()}
                  </td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums text-stone-700">
                    {s.area_ha.toFixed(1)}
                  </td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums text-stone-700">
                    {(s.area_share * 100).toFixed(1)}%
                  </td>
                  <td className="px-2.5 py-1.5 text-right tabular-nums text-stone-700">
                    {s.mean_confidence > 0 ? `${(s.mean_confidence * 100).toFixed(0)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
