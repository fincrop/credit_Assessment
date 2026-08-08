'use client';

import { triStateLabel } from '../../lib/formatRisk';

export type FarmInfoSummary = {
  farmer_id: string;
  name?: string | null;
  mobile?: string | null;
  state?: string | null;
  district?: string | null;
  village?: string | null;
  farmer_benefits?: {
    pm_kisan_enrolled?: boolean | null;
    has_crop_insurance?: boolean | null;
  } | null;
};

function benefitChipClass(v: boolean | null | undefined): string {
  if (v === true) return 'bg-emerald-50 border-emerald-200 text-emerald-800';
  if (v === false) return 'bg-stone-100 border-stone-200 text-stone-600';
  return 'bg-amber-50 border-amber-200 text-amber-900';
}

export function FarmerIdentityCard({
  farmInfo,
  farmerId,
  plotsLabel,
  assessedLabel,
  portfolioSummary,
  pmKisan,
  cropInsurance,
}: {
  farmInfo: FarmInfoSummary | null;
  farmerId: string;
  plotsLabel?: string;
  assessedLabel?: string;
  /** e.g. "6 plots · 4 scored · 1 leased · 1 excluded" */
  portfolioSummary?: string;
  pmKisan?: boolean | null;
  cropInsurance?: boolean | null;
}) {
  const name = farmInfo?.name || 'Farmer';
  const mobile = farmInfo?.mobile;
  const location = [farmInfo?.village, farmInfo?.district, farmInfo?.state]
    .filter(Boolean)
    .join(', ');

  const pm = pmKisan ?? farmInfo?.farmer_benefits?.pm_kisan_enrolled ?? null;
  const ins =
    cropInsurance ?? farmInfo?.farmer_benefits?.has_crop_insurance ?? null;

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-4 h-full flex flex-col">
      <div className="flex items-start gap-2.5 mb-3">
        <div className="w-9 h-9 rounded-full bg-emerald-100 border border-emerald-200 flex items-center justify-center text-base shrink-0">
          👤
        </div>
        <div className="min-w-0">
          <p className="text-[10px] text-stone-400 font-semibold uppercase tracking-wider">
            Farmer
          </p>
          <p className="text-base font-bold text-stone-900 truncate">{name}</p>
          <p className="text-[11px] font-mono text-stone-500 mt-0.5">{farmerId}</p>
        </div>
      </div>

      <dl className="space-y-2 text-sm flex-1">
        {mobile && (
          <div className="flex justify-between gap-2">
            <dt className="text-stone-500">Mobile</dt>
            <dd className="font-mono text-stone-800">{mobile}</dd>
          </div>
        )}
        {location && (
          <div className="flex justify-between gap-2">
            <dt className="text-stone-500 shrink-0">Location</dt>
            <dd className="text-stone-800 text-right">{location}</dd>
          </div>
        )}
        {plotsLabel && (
          <div className="flex justify-between gap-2">
            <dt className="text-stone-500">Plots</dt>
            <dd className="font-semibold text-stone-800">{plotsLabel}</dd>
          </div>
        )}
        {assessedLabel && (
          <div className="flex justify-between gap-2">
            <dt className="text-stone-500">Assessed</dt>
            <dd className="text-stone-600 text-xs font-mono text-right">{assessedLabel}</dd>
          </div>
        )}
      </dl>

      <div className="flex flex-wrap gap-2 mt-3 pt-2.5 border-t border-[#E4DFD4]">
        <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium border ${benefitChipClass(pm)}`}>
          PM-KISAN: {triStateLabel(pm)}
        </span>
        <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium border ${benefitChipClass(ins)}`}>
          Crop Insurance: {triStateLabel(ins)}
        </span>
      </div>

      {portfolioSummary && (
        <p className="mt-2.5 text-[11px] text-stone-500 leading-relaxed border-t border-dashed border-[#E4DFD4] pt-2.5">
          {portfolioSummary}
        </p>
      )}
    </div>
  );
}
