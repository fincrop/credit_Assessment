'use client';

import { useState } from 'react';
import type { RasterOverlaySpec } from '../../classification/components/AoiMap';
import {
  RASTER_PRODUCT_LABELS,
  isRampLegend,
  rasterFileUrl,
  type MonitoringResult,
  type RasterProduct,
  type RasterProductKind,
} from '../types';

export interface RasterSelection {
  product: string;
  /** null for products written once per run (sowing date, stage). */
  date: string | null;
  opacity: number;
  /** Raster hidden; field outlines show their stress colour again. */
  hidden: boolean;
}

const ORDER = Object.keys(RASTER_PRODUCT_LABELS) as RasterProductKind[];

function productLabel(product: string): string {
  return RASTER_PRODUCT_LABELS[product as RasterProductKind] || product.replace(/_/g, ' ');
}

/** Products the result can draw: a PNG and bounds are both needed. */
export function drawableProducts(result: MonitoringResult | null): RasterProduct[] {
  return (result?.rasters?.products || []).filter(
    (p) => Boolean(p.png) && Array.isArray(p.bounds) && p.bounds.length === 2
  );
}

/** Dates of one product, oldest first. Only dates the engine actually wrote. */
export function productDates(products: RasterProduct[], product: string): RasterProduct[] {
  return products
    .filter((p) => p.product === product)
    .sort((a, b) => String(a.date ?? '').localeCompare(String(b.date ?? '')));
}

/** Start on the latest stress map when there is one (the bank-report view), else the latest NDVI. */
export function defaultRasterSelection(products: RasterProduct[]): RasterSelection | null {
  if (!products.length) return null;
  const kinds = new Set(products.map((p) => p.product));
  const product =
    ['stress_class', 'anomaly', 'ndvi'].find((k) => kinds.has(k)) ||
    ORDER.find((k) => kinds.has(k)) ||
    products[0].product;
  const dates = productDates(products, product);
  return { product, date: dates[dates.length - 1]?.date ?? null, opacity: 0.85, hidden: false };
}

export function selectedProduct(
  products: RasterProduct[],
  selection: RasterSelection | null
): RasterProduct | null {
  if (!selection) return null;
  const dates = productDates(products, selection.product);
  return dates.find((p) => (p.date ?? null) === selection.date) || dates[dates.length - 1] || null;
}

export function overlayFor(
  jobId: string,
  product: RasterProduct | null,
  selection: RasterSelection | null
): RasterOverlaySpec | null {
  if (!product?.png || !product.bounds || !selection || selection.hidden) return null;
  return { url: rasterFileUrl(jobId, product.png), bounds: product.bounds, opacity: selection.opacity };
}

function Legend({ product }: { product: RasterProduct }) {
  const legend = product.legend;
  if (isRampLegend(legend)) {
    return (
      <div>
        <div
          className="h-2.5 w-full rounded-sm border border-rule"
          style={{ background: `linear-gradient(to right, ${legend.ramp.join(', ')})` }}
          aria-hidden
        />
        <div className="flex justify-between text-[10px] text-stone-600 tabular-nums mt-0.5">
          <span>{legend.vmin}</span>
          {legend.units ? <span className="text-stone-500">{legend.units}</span> : null}
          <span>{legend.vmax}</span>
        </div>
      </div>
    );
  }
  if (Array.isArray(legend) && legend.length) {
    return (
      <div>
        {legend.map((entry) => (
          <div key={String(entry.value)} className="classification-legend-row" style={{ marginTop: 3 }}>
            <span className="classification-legend-swatch" style={{ background: entry.color }} aria-hidden />
            <span className="truncate">{entry.label}</span>
            <span />
          </div>
        ))}
      </div>
    );
  }
  return <p className="text-[10px] text-stone-500">No legend supplied.</p>;
}

/**
 * Product picker, date slider, opacity and legend for the raster overlay.
 * Colour scales are the engine's fixed ones; this panel never re-stretches.
 */
export function RasterControl({
  products,
  selection,
  onChange,
}: {
  products: RasterProduct[];
  selection: RasterSelection;
  onChange: (next: RasterSelection) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const kinds = [
    ...ORDER.filter((k) => products.some((p) => p.product === k)),
    ...[...new Set(products.map((p) => p.product))].filter((k) => !ORDER.includes(k as RasterProductKind)),
  ];
  const dates = productDates(products, selection.product);
  const current = selectedProduct(products, selection);
  const index = Math.max(0, dates.findIndex((p) => p === current));

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="rounded-lg bg-white/95 border border-rule shadow-raised px-3 py-1.5 text-xs font-semibold text-stone-800 hover:border-emerald-500"
      >
        Map layers · {productLabel(selection.product)}
      </button>
    );
  }

  return (
    <div className="w-64 max-w-[calc(100vw-3rem)] rounded-[10px] border border-rule bg-[rgba(255,254,250,0.95)] shadow-raised px-3 py-2.5 text-[11px] text-stone-800 space-y-2 max-h-[min(60dvh,420px)] overflow-y-auto">
      <div className="flex items-center justify-between gap-2">
        <p className="classification-legend-title" style={{ marginBottom: 0 }}>Raster layer</p>
        <div className="flex items-center gap-2">
          <label className="inline-flex items-center gap-1 text-[10px] text-stone-600">
            <input
              type="checkbox"
              checked={!selection.hidden}
              onChange={(e) => onChange({ ...selection, hidden: !e.target.checked })}
            />
            Show
          </label>
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="text-stone-400 hover:text-stone-700 text-sm leading-none"
            aria-label="Collapse raster panel"
          >
            −
          </button>
        </div>
      </div>

      <select
        value={selection.product}
        onChange={(e) => {
          const next = productDates(products, e.target.value);
          onChange({ ...selection, product: e.target.value, date: next[next.length - 1]?.date ?? null, hidden: false });
        }}
        className="w-full rounded-lg border border-rule bg-white px-2 py-1.5 text-xs"
        aria-label="Raster product"
      >
        {kinds.map((k) => (
          <option key={k} value={k}>{productLabel(k)}</option>
        ))}
      </select>

      {dates.length > 1 ? (
        <label className="block">
          <span className="flex justify-between text-[10px] text-stone-500">
            <span>Clear dates ({dates.length})</span>
            <span className="font-mono text-stone-800">{current?.date || '—'}</span>
          </span>
          <input
            type="range"
            min={0}
            max={dates.length - 1}
            step={1}
            value={index}
            onChange={(e) => onChange({ ...selection, date: dates[Number(e.target.value)]?.date ?? null })}
            className="w-full"
            aria-label="Observation date"
          />
          <span className="flex justify-between text-[10px] text-stone-400 font-mono">
            <span>{dates[0]?.date?.slice(5) || ''}</span>
            <span>{dates[dates.length - 1]?.date?.slice(5) || ''}</span>
          </span>
        </label>
      ) : null}

      <label className="block">
        <span className="flex justify-between text-[10px] text-stone-500">
          <span>Opacity</span>
          <span className="tabular-nums">{Math.round(selection.opacity * 100)}%</span>
        </span>
        <input
          type="range"
          min={0.1}
          max={1}
          step={0.05}
          value={selection.opacity}
          onChange={(e) => onChange({ ...selection, opacity: Number(e.target.value) })}
          className="w-full"
          aria-label="Raster opacity"
        />
      </label>

      {current ? (
        <div className="pt-1.5 border-t border-rule space-y-1">
          <Legend product={current} />
          <p className="text-[10px] text-stone-600 leading-snug">
            {current.date ? <span className="font-mono">{current.date}</span> : 'This run'}
            {current.sensor ? ` · ${current.sensor}` : ''}
            {typeof current.clear_fraction === 'number'
              ? ` · ${Math.round(current.clear_fraction * 100)}% clear`
              : ''}
          </p>
          <p className="text-[10px] text-stone-500 leading-snug">Hatched = not observed (cloud / no data)</p>
          {current.note ? <p className="text-[10px] text-stone-500 leading-snug">{current.note}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
