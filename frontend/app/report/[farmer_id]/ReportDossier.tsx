'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import type { ReportPayload } from '../../types/report';
import { formatGateMultiplier, formatScoreOne, subIndexLabel, triStateLabel } from '../../lib/formatRisk';
import {
  kbsBandForScore,
  KBS_BANDS,
  KBS_MAX,
  KBS_MIN,
  kbsNormalized,
  bandChipStyle,
  type KbsBand,
} from '../../lib/kbsScore';
import { NdviTrajectory } from '../../dashboard/components/NdviTrajectory';
import { FootprintBanner } from '../../dashboard/components/ConfidenceBadge';
import { RefusalPanel } from '../../dashboard/components/RefusalPanel';
import { terminalStateOfReport } from '../../lib/terminalState';
import { hasSection } from '../../lib/reportClient';

export type FarmerRecord = {
  name?: string | null;
  mobile?: string | null;
  village?: string | null;
  district?: string | null;
  state?: string | null;
};

function prettyDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function maskMobile(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const d = raw.replace(/\D/g, '');
  if (d.length < 8) return raw;
  const last10 = d.slice(-10);
  return `+91 ${last10.slice(0, 2)}•••••${last10.slice(-2)}`;
}

function isTemplateDump(text: string | null | undefined): boolean {
  if (!text) return true;
  const t = text.trim();
  return (
    t.includes('AGRONOMIC CREDIT-RISK ASSESSMENT') ||
    t.includes('SUB-INDEX BREAKDOWN') ||
    t.includes('================')
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-muted">{children}</p>
  );
}

function MetaCell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-muted">{label}</p>
      <p className="text-[13px] font-semibold text-ink mt-0.5 font-mono tabular-nums">{value ?? '—'}</p>
    </div>
  );
}

function RunningFooter({
  report,
  page,
  pages,
}: {
  report: ReportPayload;
  page: number;
  pages: number;
}) {
  return (
    <div className="report-running text-[8px] uppercase tracking-[0.14em] text-ink-muted flex items-end justify-between gap-3 pt-4 border-t border-rule">
      <span>Krishi Bhoomi Score · {report.methodology?.index_version || 'index_v5'}</span>
      <span className="font-mono">
        {report.report_id} · {prettyDate(report.assessment_date)}
      </span>
      <span>
        {page} / {pages}
      </span>
    </div>
  );
}

function Page({
  children,
  report,
  page,
  pages = 5,
}: {
  children: React.ReactNode;
  report: ReportPayload;
  page: number;
  pages?: number;
}) {
  return (
    <article className="report-page">
      <div className="report-page-body">{children}</div>
      <RunningFooter report={report} page={page} pages={pages} />
    </article>
  );
}

function ScaleBar({ kbs, band }: { kbs: number; band: KbsBand }) {
  const t = kbsNormalized(kbs);
  return (
    <div className="mt-4">
      <div className="flex h-2.5 rounded-full overflow-hidden">
        {KBS_BANDS.map((b) => (
          <div key={b.id} className="flex-1" style={{ background: b.color }} />
        ))}
      </div>
      <div className="relative h-5 mt-1">
        <span className="absolute text-[10px] font-mono text-ink-muted" style={{ left: 0 }}>
          {KBS_MIN}
        </span>
        <span
          className="absolute text-[10px] font-bold font-mono -translate-x-1/2"
          style={{ left: `${t * 100}%`, color: band.ink }}
        >
          {kbs}
        </span>
        <span className="absolute text-[10px] font-mono text-ink-muted right-0">{KBS_MAX}</span>
      </div>
    </div>
  );
}

function benefitClass(v: boolean | null | undefined): string {
  if (v === true) return 'bg-emerald-50 border-emerald-200 text-emerald-800';
  if (v === false) return 'bg-stone-100 border-stone-200 text-stone-600';
  return 'bg-amber-50 border-amber-200 text-amber-900';
}

function polarityGlyph(polarity: string | undefined): string {
  const p = (polarity || '').toLowerCase();
  if (p === 'positive') return '▲';
  if (p === 'negative') return '▼';
  return '●';
}

function executiveSummary(
  report: ReportPayload,
  identity: FarmerRecord | null,
  band: KbsBand | null
): string {
  const narrative = report.narrative?.text;
  if (narrative && !isTemplateDump(narrative)) return narrative;

  const name = identity?.name || 'This farmer';
  const n = report.holding?.n_plots_total ?? report.parcels?.length;
  const place = [identity?.village, identity?.district, identity?.state].filter(Boolean).join(', ');
  const weakest = report.sub_indices.find((s) => s.is_weakest);
  const parts: string[] = [];
  parts.push(
    `${name}${n ? `’s ${n} parcel${n === 1 ? '' : 's'}` : ''}${place ? ` near ${place}` : ''} ${
      report.score.kbs != null && band
        ? `resolve to a Krishi Bhoomi Score of ${report.score.kbs} · ${band.name}.`
        : 'were assessed.'
    }`
  );
  if (weakest?.caption) {
    parts.push(`${subIndexLabel(weakest.key)} is the weakest channel: ${weakest.caption}`);
  }
  const pm = report.benefits?.pm_kisan;
  const ins = report.benefits?.has_crop_insurance;
  if (pm === true || ins === true) {
    const schemes = [pm === true ? 'PM-KISAN' : null, ins === true ? 'crop insurance' : null].filter(Boolean);
    parts.push(`Government-scheme presence: ${schemes.join(' and ')}.`);
  }
  parts.push(
    'This is an agronomic field-health index for the credit committee to combine with its own financial checks — not a loan amount, a credit score, or a probability of default.'
  );
  return parts.join(' ');
}

function agronomicNote(report: ReportPayload, band: KbsBand | null): string {
  const weakest = report.sub_indices.find((s) => s.is_weakest);
  if (band?.id === 'poor' || band?.id === 'fair') {
    return [
      `Field health sits in the ${band.name} band.`,
      weakest
        ? `${subIndexLabel(weakest.key)} is the weakest driver${weakest.score != null ? ` (${Math.round(weakest.score)}/100)` : ''}.`
        : '',
      weakest?.caption || '',
      'Re-run after the next observation window if a later canopy reading is needed. This is agronomic evidence, not a disbursement decision.',
    ]
      .filter(Boolean)
      .join(' ');
  }
  return [
    `Field health sits in the ${band?.name || 'assessed'} band.`,
    weakest?.caption ? `Watch ${subIndexLabel(weakest.key).toLowerCase()}: ${weakest.caption}` : '',
    'Use this alongside financial due diligence. The index does not approve or decline a loan.',
  ]
    .filter(Boolean)
    .join(' ');
}

export function ReportDossier({
  report,
  identity,
  farmerId,
  plotKey,
}: {
  report: ReportPayload;
  identity: FarmerRecord | null;
  farmerId: string;
  plotKey?: string;
}) {
  const refusal = useMemo(() => terminalStateOfReport(report), [report]);
  const band = kbsBandForScore(report.score.kbs);
  const kbs = report.score.kbs;
  const location = [identity?.village, identity?.district, identity?.state].filter(Boolean).join(', ');
  const mobile = maskMobile(identity?.mobile);
  const summary = executiveSummary(report, identity, band);
  const gate = report.score.confidence_gate;
  const obs = report.observations;
  const holding = report.holding;
  const parcels = report.parcels ?? [];
  const wx = report.weather_snapshot;
  const hash = report.integrity?.content_sha256?.slice(0, 12);

  const windowLabel =
    obs?.window_start && obs?.window_end
      ? `${prettyDate(obs.window_start)} – ${prettyDate(obs.window_end)}`
      : undefined;

  return (
    <div className="report-dossier">
      <div className="no-print flex items-center justify-end gap-2 mb-3 max-w-[210mm] mx-auto">
        <Link
          href={`/dashboard?farmer_id=${encodeURIComponent(farmerId)}`}
          className="text-[12px] font-semibold text-ink-2 border border-rule rounded-md px-2.5 py-1.5 hover:bg-paper-raised transition-colors"
        >
          Dashboard
        </Link>
        <button
          type="button"
          onClick={() => window.print()}
          className="text-[12px] font-semibold bg-accent text-white rounded-md px-3 py-1.5 hover:opacity-90 transition-opacity"
        >
          Print / PDF
        </button>
      </div>

      {!refusal.scorable && (
        <div className="max-w-[210mm] mx-auto mb-4">
          {refusal.state === 'PENDING' ? (
            <section className="rounded-xl border border-rule bg-card px-5 py-4">
              <Eyebrow>No score produced</Eyebrow>
              <p className="text-[13px] text-ink mt-1.5 leading-snug">
                This assessment ended in <span className="font-mono">{report.status}</span>. No Krishi
                Bhoomi Score was produced.
              </p>
            </section>
          ) : (
            <RefusalPanel verdict={refusal} />
          )}
        </div>
      )}

      {/* ── 1. Cover / farmer record / executive summary ── */}
      <Page report={report} page={1}>
        <header className="flex items-start justify-between gap-4 border-b-2 border-ink pb-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-ink-muted">
              01 — Farmer Assessment Report
            </p>
            <h1 className="text-[22px] font-bold tracking-tight text-ink mt-1">Krishi Bhoomi Score</h1>
            <p className="text-[12px] text-ink-2 mt-0.5">Score dossier</p>
          </div>
          <div className="grid grid-cols-3 gap-4 text-right">
            <MetaCell label="Issued" value={prettyDate(report.assessment_date)} />
            <MetaCell
              label="Model"
              value={`${report.methodology?.pipeline_version || 'v5'} · ${report.methodology?.index_version || 'idx_v5'}`}
            />
            <MetaCell label="Gate" value={formatGateMultiplier(gate)} />
          </div>
        </header>

        <section className="mt-5 grid grid-cols-[1.4fr_0.9fr] gap-6">
          <div>
            <Eyebrow>Farmer record · {report.farmer_id}</Eyebrow>
            <h2 className="text-[20px] font-bold text-ink mt-1 leading-tight">
              {identity?.name || 'Farmer identity on file'}
            </h2>
            <p className="text-[13px] text-ink-2 mt-1.5 leading-snug max-w-md">
              {location || 'Location is taken from the farmer record held with this assessment.'}
            </p>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
              {mobile && (
                <>
                  <dt className="text-ink-muted">Mobile</dt>
                  <dd className="font-mono text-ink">{mobile}</dd>
                </>
              )}
              <dt className="text-ink-muted">Registered id</dt>
              <dd className="font-mono text-ink truncate">{report.farmer_id}</dd>
              {plotKey && (
                <>
                  <dt className="text-ink-muted">Plot</dt>
                  <dd className="font-mono text-ink truncate">{plotKey}</dd>
                </>
              )}
            </dl>
            <div className="flex flex-wrap gap-1.5 mt-3">
              <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full border ${benefitClass(report.benefits?.pm_kisan)}`}>
                PM-KISAN · {triStateLabel(report.benefits?.pm_kisan)}
              </span>
              <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full border ${benefitClass(report.benefits?.has_crop_insurance)}`}>
                Crop insurance · {triStateLabel(report.benefits?.has_crop_insurance)}
              </span>
            </div>
          </div>
          <div
            className="rounded-xl border px-4 py-3 flex flex-col justify-center"
            style={{
              background: band?.surface ?? '#F0EDE6',
              borderColor: band?.border ?? '#E4DFD4',
            }}
          >
            <p className="text-[10px] font-bold uppercase tracking-[0.16em]" style={{ color: band?.ink }}>
              Holding · {band?.name || 'Unscored'}
            </p>
            <p className="text-[40px] font-bold font-mono tabular-nums leading-none mt-1" style={{ color: band?.ink }}>
              {kbs ?? '—'}
            </p>
            <p className="text-[11px] text-ink-2 mt-1">
              {band ? `${band.name} · ${band.min}–${band.max}` : 'No Krishi Bhoomi Score'}
            </p>
          </div>
        </section>

        <section className="mt-6">
          <Eyebrow>Executive summary</Eyebrow>
          <p className="text-[13px] text-ink leading-relaxed mt-2 max-w-3xl">{summary}</p>
        </section>

        <p className="mt-8 text-[9px] uppercase tracking-[0.14em] text-ink-muted leading-relaxed">
          Confidential · for credit committee use · agronomic index only · contains modified Copernicus
          Sentinel-2 data
        </p>
      </Page>

      {/* ── 2. Score, observations, holding table ── */}
      <Page report={report} page={2}>
        <FootprintBanner footprint={report.footprint} />
        <div className="grid grid-cols-[1.1fr_0.9fr] gap-6 items-start">
          <section>
            <Eyebrow>Krishi Bhoomi Score</Eyebrow>
            <div className="flex items-end gap-3 mt-1">
              <p className="text-[56px] font-bold font-mono tabular-nums leading-none" style={{ color: band?.ink }}>
                {kbs ?? '—'}
              </p>
              {band && (
                <span className="mb-2 text-[12px] font-bold px-2 py-0.5 rounded-full border" style={bandChipStyle(band)}>
                  {band.name} · {band.min}–{band.max}
                </span>
              )}
            </div>
            {kbs != null && band && <ScaleBar kbs={kbs} band={band} />}
            <p className="text-[12px] text-ink-2 mt-3 leading-snug max-w-md">
              Weighted from {report.sub_indices.length || 4} sub-indices
              {windowLabel ? ` across ${windowLabel}` : ''}. Confidence gate applied for observation
              coverage. Scale {KBS_MIN}–{KBS_MAX}.
            </p>
          </section>
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-rule bg-paper/50 px-3 py-2.5">
              <MetaCell label="Raw index" value={formatScoreOne(report.score.raw_index)} />
              <p className="text-[10px] text-ink-muted mt-1">Before confidence gate</p>
            </div>
            <div className="rounded-lg border border-rule bg-paper/50 px-3 py-2.5">
              <MetaCell label="Confidence gate" value={formatGateMultiplier(gate)} />
              <p className="text-[10px] text-ink-muted mt-1">
                {obs?.observed_fraction != null
                  ? `${Math.round(obs.observed_fraction * 100)}% observed`
                  : 'Observation coverage'}
              </p>
            </div>
            <div className="rounded-lg border border-rule bg-paper/50 px-3 py-2.5">
              <MetaCell
                label="Observations"
                value={
                  obs?.n_present != null && obs?.n_total != null
                    ? `${obs.n_present} / ${obs.n_total}`
                    : obs?.n_present ?? '—'
                }
              />
              <p className="text-[10px] text-ink-muted mt-1">
                {obs?.satellite_provider || 'Satellite scenes'}
              </p>
            </div>
            <div className="rounded-lg border border-rule bg-paper/50 px-3 py-2.5">
              <MetaCell
                label="Trend"
                value={
                  report.trend
                    ? `${report.trend.delta > 0 ? '+' : ''}${report.trend.delta}`
                    : 'First assessment'
                }
              />
              <p className="text-[10px] text-ink-muted mt-1">
                {report.trend
                  ? `vs ${prettyDate(report.trend.previous_date)} · was ${report.trend.previous_kbs}`
                  : 'No prior score to compare'}
              </p>
            </div>
          </div>
        </div>

        <section className="mt-6">
          <Eyebrow>
            Land holding
            {holding?.n_plots_total ? ` · ${holding.n_plots_total} parcels` : ''}
            {holding?.total_area_ha != null ? ` · ${holding.total_area_ha} ha` : ''}
          </Eyebrow>
          <div className="grid grid-cols-4 gap-3 mt-3">
            <MetaCell label="Total parcels" value={holding?.n_plots_total ?? parcels.length ?? '—'} />
            <MetaCell
              label="Owned"
              value={holding?.owned_area_ha != null ? `${holding.owned_area_ha} ha` : holding?.n_owned ?? '—'}
            />
            <MetaCell
              label="Leased"
              value={holding?.leased_area_ha != null ? `${holding.leased_area_ha} ha` : holding?.n_leased ?? '—'}
            />
            <MetaCell
              label="Centroid"
              value={
                holding?.centroid?.latitude != null && holding?.centroid?.longitude != null
                  ? `${holding.centroid.latitude.toFixed(3)}°N · ${holding.centroid.longitude.toFixed(3)}°E`
                  : '—'
              }
            />
          </div>

          {parcels.length > 0 && (
            <table className="w-full mt-4 text-[12px]">
              <thead>
                <tr className="text-left text-[9px] uppercase tracking-[0.14em] text-ink-muted border-b border-rule">
                  <th className="py-1.5 font-semibold">Parcel</th>
                  <th className="py-1.5 font-semibold">Tenure</th>
                  <th className="py-1.5 font-semibold">Area</th>
                  <th className="py-1.5 font-semibold">Major crop</th>
                  <th className="py-1.5 font-semibold text-right">KBS</th>
                  <th className="py-1.5 font-semibold text-right">Band</th>
                </tr>
              </thead>
              <tbody>
                {parcels.map((p) => (
                  <tr key={String(p.plot_key || p.farm_id)} className="border-b border-rule/70">
                    <td className="py-2 font-mono text-[11px]">{p.plot_key || p.farm_id || '—'}</td>
                    <td className="py-2">{p.tenure || '—'}</td>
                    <td className="py-2 font-mono">{p.area_ha != null ? `${p.area_ha.toFixed(2)} ha` : '—'}</td>
                    <td className="py-2">{p.crop || '—'}</td>
                    <td className="py-2 font-mono text-right font-semibold">{p.kbs ?? '—'}</td>
                    <td className="py-2 text-right">
                      {p.band ? (
                        <span
                          className="text-[10px] font-bold px-1.5 py-0.5 rounded-full border"
                          style={bandChipStyle(kbsBandForScore(p.kbs))}
                        >
                          {p.band.name}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </Page>

      {/* ── 3. Sub-indices + reason codes ── */}
      <Page report={report} page={3}>
        <Eyebrow>Score contribution · sub-indices</Eyebrow>
        <ul className="mt-4 space-y-3">
          {report.sub_indices.map((s) => {
            const pct = Math.max(0, Math.min(100, s.score ?? 0));
            const ink = kbsBandForScore(((s.score ?? 0) / 100) * 600 + 300)?.ink;
            return (
              <li key={s.key} className="border-b border-rule pb-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <p className="text-[14px] font-semibold text-ink">{subIndexLabel(s.key)}</p>
                    <p className="text-[10px] uppercase tracking-[0.12em] text-ink-muted mt-0.5">
                      {s.weight != null ? `${Math.round(s.weight)}% weight` : ''}
                      {s.is_weakest ? ' · weakest' : ''}
                    </p>
                  </div>
                  <p className="text-[22px] font-bold font-mono tabular-nums" style={{ color: ink }}>
                    {s.score != null ? Math.round(s.score) : '—'}
                    <span className="text-[11px] font-semibold text-ink-muted"> /100</span>
                  </p>
                </div>
                <div className="h-1.5 rounded-full bg-rule mt-2 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: ink }} />
                </div>
                {s.caption && <p className="text-[12px] text-ink-2 mt-1.5 leading-snug">{s.caption}</p>}
              </li>
            );
          })}
        </ul>

        {report.reason_codes.length > 0 && (
          <section className="mt-6">
            <Eyebrow>Reason codes · {report.reason_codes.length}</Eyebrow>
            <ul className="mt-3 space-y-2">
              {report.reason_codes.slice(0, 8).map((rc, i) => (
                <li key={i} className="grid grid-cols-[18px_7rem_1fr] gap-2 items-start text-[12px]">
                  <span className="text-ink-2">{polarityGlyph(rc.polarity)}</span>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-ink-muted truncate">
                    {rc.code || '—'}
                  </span>
                  <span className="text-ink leading-snug">{rc.message || rc.code}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </Page>

      {/* ── 4. NDVI, weather, methodology, agronomic note ── */}
      <Page report={report} page={4}>
        {hasSection(report, 'ndvi_trajectory') && (
          <section>
            <div className="flex items-baseline justify-between gap-3 mb-2">
              <Eyebrow>NDVI trajectory{windowLabel ? ` · ${windowLabel}` : ''}</Eyebrow>
            </div>
            <NdviTrajectory trajectory={report.ndvi_trajectory} windowLabel={windowLabel} variant="overview" />
            {report.ndvi_trajectory?.comparison_note && (
              <p className="text-[11px] text-ink-muted mt-2 leading-snug">
                Parcel line only. {report.ndvi_trajectory.comparison_note}
              </p>
            )}
          </section>
        )}

        {wx && (
          <section className="mt-5">
            <Eyebrow>Weather snapshot</Eyebrow>
            <div className="grid grid-cols-3 gap-2 mt-3">
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell
                  label="Kharif rain"
                  value={wx.kharif_avg_rainfall_mm != null ? `${Math.round(wx.kharif_avg_rainfall_mm)} mm` : '—'}
                />
              </div>
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell
                  label="Rabi rain"
                  value={wx.rabi_avg_rainfall_mm != null ? `${Math.round(wx.rabi_avg_rainfall_mm)} mm` : '—'}
                />
              </div>
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell
                  label="Dry spell"
                  value={wx.max_dry_spell_days != null ? `${Math.round(wx.max_dry_spell_days)} days` : '—'}
                />
              </div>
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell
                  label="Heat stress"
                  value={wx.max_heat_stress_days != null ? `${Math.round(wx.max_heat_stress_days)} days` : '—'}
                />
              </div>
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell label="Alerts" value={wx.total_extreme_events ?? '—'} />
              </div>
              <div className="rounded-lg border border-rule px-3 py-2">
                <MetaCell label="Weather risk" value={formatScoreOne(wx.weather_risk_score)} />
              </div>
            </div>
          </section>
        )}

        <section className="mt-5 grid grid-cols-[1.15fr_0.85fr] gap-5">
          <div>
            <Eyebrow>Methodology &amp; confidence</Eyebrow>
            <p className="text-[12px] text-ink-2 leading-relaxed mt-2">
              The Krishi Bhoomi Score reconstructs canopy, land use and weather behaviour for each parcel
              from optical satellite imagery and weather records. Four sub-indices are combined with
              expert weights, then multiplied by a confidence gate reflecting observation coverage over
              the assessment window. This is an agronomic risk index, not a probability of default or a
              loan amount. Weights are provisional.
            </p>
          </div>
          <dl className="text-[12px] space-y-1">
            <div className="flex justify-between gap-2">
              <dt className="text-ink-muted">Model</dt>
              <dd className="font-mono">{report.methodology?.index_version || '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-muted">Optical</dt>
              <dd className="font-mono">{report.methodology?.satellite_provider || '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-muted">Cloud mask</dt>
              <dd className="font-mono">{report.methodology?.cloud_mask_version || '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-muted">Window</dt>
              <dd className="font-mono text-right">{windowLabel || '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-muted">Pipeline</dt>
              <dd className="font-mono">{report.methodology?.pipeline_version || '—'}</dd>
            </div>
          </dl>
        </section>

        <section
          className="mt-5 rounded-xl border px-4 py-3"
          style={{ background: band?.surface ?? '#F0EDE6', borderColor: band?.border ?? '#E4DFD4' }}
        >
          <Eyebrow>Agronomic note</Eyebrow>
          <p className="text-[13px] text-ink leading-relaxed mt-1.5">{agronomicNote(report, band)}</p>
        </section>
      </Page>

      {/* ── 5. Bands, hash, disclaimer ── */}
      <Page report={report} page={5}>
        <Eyebrow>Score bands · KBS {KBS_MIN}–{KBS_MAX}</Eyebrow>
        <ul className="mt-4 grid grid-cols-2 gap-3">
          {KBS_BANDS.map((b) => {
            const here = band?.id === b.id;
            return (
              <li
                key={b.id}
                className="rounded-xl border px-4 py-3"
                style={{
                  background: here ? b.surface : '#FFFEFA',
                  borderColor: here ? b.border : '#E4DFD4',
                }}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <p className="text-[13px] font-bold uppercase tracking-[0.12em]" style={{ color: b.ink }}>
                    {b.name}
                  </p>
                  <p className="text-[12px] font-mono text-ink-2">
                    {b.min}
                    {b.id === 'excellent' ? '+' : `–${b.max}`}
                  </p>
                </div>
                <p className="text-[12px] text-ink-2 mt-1 leading-snug">{b.shortDescription}</p>
                {here && kbs != null && (
                  <p className="text-[11px] font-bold mt-2" style={{ color: b.ink }}>
                    ▸ This farmer · {kbs}
                  </p>
                )}
              </li>
            );
          })}
        </ul>

        <section className="mt-8 grid grid-cols-2 gap-6">
          <div>
            <Eyebrow>Report hash</Eyebrow>
            <p className="font-mono text-[12px] text-ink mt-2 break-all">sha256:{hash}</p>
            <p className="text-[11px] text-ink-muted mt-1">
              Generated {prettyDate(report.generated_at)} · {report.integrity?.note}
            </p>
          </div>
          <div>
            <Eyebrow>Integrity</Eyebrow>
            <p className="text-[12px] text-ink-2 mt-2 leading-snug">
              Hash covers report content, excluding the generation timestamp, so the same assessment
              always hashes the same.
            </p>
          </div>
        </section>

        <p className="mt-8 text-[11px] text-ink-2 leading-relaxed border-t border-rule pt-3">
          Disclaimer. The Krishi Bhoomi Score is an agronomic risk index derived from remote-sensing
          signals and weather data. It is not a probability of default, a loan approval, a credit rating
          or an appraisal of collateral value. Weights and thresholds are provisional. Cadastral
          boundaries may differ from revenue records. Personal identifiers such as Aadhaar are not
          included in this dossier. Contains modified Copernicus Sentinel-2 data.
        </p>
      </Page>
    </div>
  );
}
