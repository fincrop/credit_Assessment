'use client';

import { STRESS_COLORS, type MonitoringResult, type MonitorZone } from '../types';

function num(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

function ZoneCard({ zone }: { zone: MonitorZone }) {
  const latest = [...(zone.intervals || [])].reverse().find((item) => item.stress?.type);
  return (
    <article className="rounded-xl border border-rule bg-white px-3 py-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-bold text-stone-900">
            {zone.zone_id} · {zone.crop}
          </p>
          <p className="text-[11px] text-stone-500">
            {zone.progress?.stage || 'Stage unknown'}
            {typeof zone.area_share === 'number' ? ` · ${Math.round(zone.area_share * 100)}% of the field` : ''}
            {zone.pixel_count ? ` · ${zone.pixel_count} pixels` : ''}
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold rounded-full border border-rule px-2 py-0.5 text-stone-700">
          <span
            className="w-2 h-2 rounded-full"
            style={{ background: STRESS_COLORS[latest?.stress?.type || ''] || STRESS_COLORS.Unspecified }}
          />
          {latest?.stress?.type || zone.progress?.stage || '—'}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <dt className="text-stone-500">Sowing</dt>
        <dd className="text-stone-800 font-medium">{zone.sowing?.date || 'Unknown'}</dd>
        <dt className="text-stone-500">Sowing range</dt>
        <dd className="text-stone-800">{zone.sowing?.early || '—'} → {zone.sowing?.late || '—'}</dd>
        <dt className="text-stone-500">Sources</dt>
        <dd className="text-stone-800">{(zone.sowing?.sources || []).join(', ') || '—'}</dd>
        <dt className="text-stone-500">Stress</dt>
        <dd className="text-stone-800">
          {latest?.stress?.type || '—'}
          {typeof latest?.stress?.stressed_fraction === 'number'
            ? ` · ${Math.round(latest.stress.stressed_fraction * 100)}%`
            : ''}
        </dd>
        <dt className="text-stone-500">Yield</dt>
        <dd className="text-stone-800">
          {zone.yield ? `${num(zone.yield.t_ha)} t/ha (${num(zone.yield.low)}–${num(zone.yield.high)})` : 'Withheld'}
        </dd>
        <dt className="text-stone-500">Harvest</dt>
        <dd className="text-stone-800">
          {zone.harvest?.observed && zone.harvest.date
            ? zone.harvest.date
            : zone.progress?.harvest ||
              (zone.progress?.harvest_window || []).filter(Boolean).join(' → ') ||
              '—'}
          {zone.harvest?.in_season === false ? ' · outside the usual months' : ''}
        </dd>
      </dl>
      {zone.sowing?.note && <p className="text-[11px] text-stone-500 leading-relaxed">{zone.sowing.note}</p>}
      {zone.split_reason && zone.split_reason !== 'boundary' && (
        <p className="text-[11px] text-stone-600 leading-relaxed">
          This parcel was split from the classified boundary on {zone.split_reason}. Its sowing, harvest, indices and yield are stored on their own.
        </p>
      )}
      {zone.indices && zone.indices.length > 0 && (
        <p className="text-[11px] text-stone-500">{zone.indices.length} satellite scenes stored for this parcel.</p>
      )}
    </article>
  );
}

function skipSummary(skipped: NonNullable<MonitoringResult['skipped']>): string {
  const noPixels = skipped.filter((item) => /no clear pixels/i.test(item.note || '')).length;
  const removed = skipped.length - noPixels;
  if (noPixels === skipped.length) {
    return `${skipped.length} farms were not scored. Satellite observations did not come back, so they were not removed for a canopy mismatch. Run this selection again.`;
  }
  const parts: string[] = [];
  if (removed) {
    parts.push(
      `${removed} boundar${removed === 1 ? 'y' : 'ies'} removed because the canopy did not match the classified crop.`,
    );
  }
  if (noPixels) {
    parts.push(`${noPixels} farm${noPixels === 1 ? '' : 's'} had no satellite sample.`);
  }
  return parts.join(' ');
}

export function ResultsPanel({ result }: { result: MonitoringResult }) {
  const zones = (result.zones || []).filter((z) => z.kind !== 'non_crop');
  const recent = zones[0]?.intervals?.slice(-6).reverse() || [];
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-bold text-stone-900">{result.name || result.crop}</h2>
        <p className="text-[11px] text-stone-500 mt-0.5">
          {result.crop} · {result.season || 'season'} · as of {result.as_of || '—'}
          {result.typed === false ? ' · crop-specific yield withheld' : ''}
        </p>
        {result.exclusion_reason && (
          <p className="text-[11px] text-stone-600 mt-1 leading-relaxed">{result.exclusion_reason}</p>
        )}
        {!!result.skipped?.length && (
          <p className="text-[11px] text-stone-600 mt-1 leading-relaxed">
            {skipSummary(result.skipped)}
          </p>
        )}
      </div>
      {result.cluster_summary && (
        <div className="rounded-xl border border-rule bg-stone-50 px-3 py-2 text-[11px] text-stone-700 leading-relaxed">
          <p className="font-semibold text-stone-900">
            {result.cluster_summary.cluster_id || 'Cluster'}
            {result.cluster_summary.village ? ` · ${result.cluster_summary.village}` : ''}
          </p>
          <p>
            {result.cluster_summary.farm_count ?? 0} farms kept
            {typeof result.cluster_summary.mean_yield_t_ha === 'number'
              ? ` · mean yield ${result.cluster_summary.mean_yield_t_ha.toFixed(2)} t/ha`
              : ''}
            {result.cluster_summary.sowing_earliest
              ? ` · sowing ${result.cluster_summary.sowing_earliest} to ${result.cluster_summary.sowing_latest || result.cluster_summary.sowing_earliest}`
              : ''}
          </p>
          {!!result.cluster_summary.stress_counts && (
            <p>
              {Object.entries(result.cluster_summary.stress_counts)
                .map(([label, count]) => `${label} ${count}`)
                .join(' · ')}
            </p>
          )}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        {[
          ['Farms', String(result.farm_count ?? result.farms?.length ?? result.zone_count ?? zones.length)],
          ['Confidence', typeof result.confidence === 'number' ? `${Math.round(result.confidence * 100)}%` : '—'],
          ['Reference', (result.reference_pool || '—').replace(/_/g, ' ')],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-rule bg-stone-50 px-2 py-2">
            <p className="text-[10px] uppercase tracking-wide text-stone-500">{label}</p>
            <p className="text-sm font-semibold text-stone-900 truncate">{value}</p>
          </div>
        ))}
      </div>
      {(result.farms && result.farms.length > 1) ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] text-left">
            <thead className="text-stone-500">
              <tr>
                <th className="py-1 pr-2 font-medium">Crop</th>
                <th className="py-1 pr-2 font-medium">ha</th>
                <th className="py-1 pr-2 font-medium">Sowing</th>
                <th className="py-1 pr-2 font-medium">Harvest</th>
                <th className="py-1 font-medium">Yield</th>
              </tr>
            </thead>
            <tbody>
              {result.farms.slice(0, 40).map((farm) => (
                <tr key={farm.field_id} className="border-t border-rule">
                  <td className="py-1 pr-2">{farm.crop}</td>
                  <td className="py-1 pr-2">{typeof farm.area_ha === 'number' ? farm.area_ha.toFixed(2) : '—'}</td>
                  <td className="py-1 pr-2">{farm.sowing_date || '—'}</td>
                  <td className="py-1 pr-2">{farm.harvest_date || '—'}</td>
                  <td className="py-1">{typeof farm.yield_t_ha === 'number' ? farm.yield_t_ha.toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.farms.length > 40 && (
            <p className="text-[11px] text-stone-500 mt-1">Showing 40 of {result.farms.length}. The download has every farm.</p>
          )}
        </div>
      ) : zones.map((zone) => (
        <ZoneCard key={zone.zone_id} zone={zone} />
      ))}
      {recent.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">
            Recent observations · {zones[0]?.zone_id}
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-left">
              <thead className="text-stone-500">
                <tr>
                  <th className="py-1 pr-2 font-medium">Date</th>
                  <th className="py-1 pr-2 font-medium">View</th>
                  <th className="py-1 pr-2 font-medium">Cover</th>
                  <th className="py-1 pr-2 font-medium">Stress</th>
                  <th className="py-1 font-medium">N</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((item) => (
                  <tr key={item.date} className="border-t border-rule">
                    <td className="py-1 pr-2 font-mono">{item.date.slice(5)}</td>
                    <td className="py-1 pr-2">{item.kind}</td>
                    <td className="py-1 pr-2">{num(item.cover, 2)}</td>
                    <td className="py-1 pr-2">{item.stress?.type || '—'}</td>
                    <td className="py-1">{item.nitrogen?.band || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {(result.limits || []).slice(0, 3).map((line) => (
        <p key={line} className="text-[11px] text-stone-500 leading-relaxed">
          {line}
        </p>
      ))}
    </div>
  );
}
