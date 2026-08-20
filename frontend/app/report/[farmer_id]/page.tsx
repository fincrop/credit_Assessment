'use client';

import { Suspense, use, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useReport } from '../../lib/useReport';
import { ReportDossier, type FarmerRecord } from './ReportDossier';

function ReportContent({ farmerId }: { farmerId: string }) {
  const searchParams = useSearchParams();
  const plotKey = searchParams.get('plot_key')?.trim() || undefined;
  const { report, loading, problem } = useReport(farmerId, { plotKey });
  const [identity, setIdentity] = useState<FarmerRecord | null>(null);

  useEffect(() => {
    const id = farmerId.trim();
    if (!id) return;
    const ctrl = new AbortController();
    fetch(`/api/farm-info/${encodeURIComponent(id)}`, {
      credentials: 'include',
      signal: ctrl.signal,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        const fi = json?.farm_info;
        if (!fi) return;
        setIdentity({
          name: fi.name ?? null,
          mobile: fi.mobile ?? null,
          village: fi.village ?? null,
          district: fi.district ?? null,
          state: fi.state ?? null,
        });
      })
      .catch(() => {
        /* identity is optional — the dossier still renders on farmer_id */
      });
    return () => ctrl.abort();
  }, [farmerId]);

  if (loading) {
    return <p className="text-sm text-ink-muted max-w-[210mm] mx-auto">Loading report…</p>;
  }

  if (problem) {
    return (
      <div className="max-w-[210mm] mx-auto rounded-xl border border-rule bg-card px-5 py-6">
        <h1 className="text-base font-semibold text-ink">Report unavailable</h1>
        <p className="text-[13px] text-ink-2 mt-1.5">{problem.message}</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="max-w-[210mm] mx-auto rounded-xl border border-rule bg-card px-5 py-6">
        <h1 className="text-base font-semibold text-ink">No report for this farmer</h1>
        <p className="text-[13px] text-ink-2 mt-1.5 max-w-xl leading-relaxed">
          No assessment has been stored for <span className="font-mono">{farmerId}</span>. Run an
          assessment first — the report is read-only and never triggers one.
        </p>
      </div>
    );
  }

  return (
    <ReportDossier report={report} identity={identity} farmerId={farmerId} plotKey={plotKey} />
  );
}

export default function ReportPage({
  params,
}: {
  params: Promise<{ farmer_id: string }>;
}) {
  const { farmer_id } = use(params);
  const farmerId = decodeURIComponent(farmer_id);

  return (
    <main className="min-h-screen bg-paper py-6 px-4 overflow-x-auto print:p-0 print:bg-white">
      <Suspense fallback={<p className="text-sm text-ink-muted max-w-[210mm] mx-auto">Loading report…</p>}>
        <ReportContent farmerId={farmerId} />
      </Suspense>
    </main>
  );
}
