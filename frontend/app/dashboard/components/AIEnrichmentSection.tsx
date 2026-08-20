'use client';

import type { AssessmentPayload, AiCounterfactuals, ReasonCode } from '../../types/assessment';
import { formatNumber, humanizeKey } from '../../lib/format';
import { useRiskView } from '../../lib/useRiskView';
import { subIndexLabel } from '../../lib/formatRisk';
import { kbsBandForScore, toKbsScore } from '../../lib/kbsScore';

const LANG_NAMES: Record<string, string> = {
  hi: 'Hindi',
  te: 'Telugu',
  mr: 'Marathi',
  ta: 'Tamil',
  kn: 'Kannada',
  gu: 'Gujarati',
  pa: 'Punjabi',
  bn: 'Bengali',
  or: 'Odia',
  ml: 'Malayalam',
};

function isTemplateDump(text: string | null | undefined): boolean {
  if (!text) return true;
  const t = text.trim();
  if (t.startsWith('[Translation unavailable')) return true;
  return (
    t.includes('AGRONOMIC CREDIT-RISK ASSESSMENT') ||
    t.includes('SUB-INDEX BREAKDOWN') ||
    t.includes('================')
  );
}

function polarityMeta(polarity: string | undefined): {
  word: string;
  ink: string;
  surface: string;
  border: string;
} {
  const p = (polarity || '').toLowerCase();
  if (p === 'positive') {
    return { word: 'Helped', ink: '#006446', surface: '#DFF0E9', border: '#C4E2D6' };
  }
  if (p === 'negative') {
    return { word: 'Hurt', ink: '#9A2E1F', surface: '#FAE8E4', border: '#F2D2CB' };
  }
  return { word: 'Note', ink: '#7A5405', surface: '#FCF0D9', border: '#F5E2B8' };
}

function prettyFeasibility(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const k = raw.toLowerCase();
  if (k.includes('high') || k.includes('easy')) return 'Easier to change';
  if (k.includes('low') || k.includes('hard')) return 'Harder to change';
  if (k.includes('medium') || k.includes('moderate')) return 'Possible';
  return raw.replace(/_/g, ' ');
}

function recCopy(category: string | null | undefined): string {
  const c = String(category || '').toUpperCase();
  if (c === 'LOW') return 'The agronomic picture is favourable — it supports standard lending consideration.';
  if (c === 'MEDIUM') return 'Agronomic risk is moderate — worth a closer look, not a stop by itself.';
  if (c === 'HIGH') return 'Agronomic risk is elevated — safeguards and closer monitoring are reasonable.';
  if (c === 'VERY_HIGH') return 'Agronomic risk is high — re-check after more seasons before relying on this plot alone.';
  return 'Use the findings below alongside the lender’s own financial checks.';
}

function ReasonCard({ rc }: { rc: ReasonCode }) {
  const meta = polarityMeta(rc.polarity);
  return (
    <li className="rounded-lg border px-3 py-2.5" style={{ background: meta.surface, borderColor: meta.border }}>
      <p className="text-[10px] font-bold uppercase tracking-wider" style={{ color: meta.ink }}>
        {meta.word}
      </p>
      <p className="text-[13px] text-stone-700 mt-1 leading-snug">{rc.message || rc.code || '—'}</p>
    </li>
  );
}

function DriverRow({
  label,
  contribution,
  maxAbs,
  positive,
}: {
  label: string;
  contribution?: number;
  maxAbs: number;
  positive: boolean;
}) {
  const mag = contribution != null ? Math.abs(contribution) : 0;
  const pct = maxAbs > 0 ? Math.round((mag / maxAbs) * 100) : 0;
  return (
    <li>
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[13px] text-stone-700 truncate">{label}</p>
      </div>
      <div className="h-1.5 rounded-full bg-stone-200/80 mt-1.5">
        <div
          className="h-1.5 rounded-full"
          style={{
            width: `${Math.max(8, pct)}%`,
            background: positive ? '#15803D' : '#B45309',
          }}
        />
      </div>
    </li>
  );
}

export function AIEnrichmentSection({ data }: { data: AssessmentPayload }) {
  const ai = data.ai_enrichment;
  const view = useRiskView(data);
  const ex = ai?.explainability ?? ai?.explainability_mongo;
  const cf = (ai?.counterfactuals ?? ai?.counterfactuals_mongo) as AiCounterfactuals | undefined;
  const reasons = view.reasonCodes || [];
  const helped = reasons.filter((r) => (r.polarity || '').toLowerCase() === 'positive');
  const hurt = reasons.filter((r) => (r.polarity || '').toLowerCase() === 'negative');
  const notes = reasons.filter((r) => {
    const p = (r.polarity || '').toLowerCase();
    return p !== 'positive' && p !== 'negative';
  });

  const english = ai?.english_narrative;
  const translated = ai?.translated_narrative;
  const showEnglish = !!english && !isTemplateDump(english);
  const showTranslated = !!translated && !isTemplateDump(translated);
  const langName = LANG_NAMES[String(ai?.translation_language || '').toLowerCase()] || null;

  const pos = ex?.top_positive_drivers ?? [];
  const neg = ex?.top_negative_drivers ?? [];
  const maxAbs = Math.max(
    0.0001,
    ...pos.map((d) => Math.abs(d.contribution ?? 0)),
    ...neg.map((d) => Math.abs(d.contribution ?? 0))
  );

  const scenarios = cf?.scenarios ?? [];
  const roadmap = cf?.improvement_roadmap;
  const roadmapSteps = Array.isArray(roadmap)
    ? roadmap
    : typeof roadmap === 'string' && roadmap.trim() && !isTemplateDump(roadmap)
      ? [{ action: roadmap }]
      : [];

  const kbs = view.score != null ? toKbsScore(view.score) : null;
  const band = kbs != null ? kbsBandForScore(kbs) : null;

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-rule p-5">
        <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Why this score</p>
        <div className="flex items-start justify-between gap-3 flex-wrap mt-0.5">
          <h2 className="text-base font-bold text-stone-900">What the assessment is saying</h2>
          {band && (
            <span
              className="text-[11px] font-bold px-2.5 py-1 rounded-full border"
              style={{ color: band.ink, background: band.surface, borderColor: band.border }}
            >
              {band.riskLabel} agronomic risk
            </span>
          )}
        </div>
        <p className="text-[13px] text-stone-600 mt-3 leading-relaxed">{recCopy(view.category)}</p>
        {ex?.credit_summary && !isTemplateDump(ex.credit_summary) && (
          <p className="text-[13px] text-stone-600 mt-2 leading-relaxed">{ex.credit_summary}</p>
        )}
      </div>

      {(helped.length > 0 || hurt.length > 0 || notes.length > 0) && (
        <div className="grid lg:grid-cols-2 gap-4">
          <div className="bg-white rounded-xl border border-rule p-5">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Helped the score</p>
            <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-3">What looked strong</h2>
            {helped.length === 0 ? (
              <p className="text-sm text-stone-500">No strength flags were stored for this plot.</p>
            ) : (
              <ul className="space-y-2">
                {helped.slice(0, 6).map((rc, i) => (
                  <ReasonCard key={i} rc={rc} />
                ))}
              </ul>
            )}
          </div>
          <div className="bg-white rounded-xl border border-rule p-5">
            <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Brought the score down</p>
            <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-3">What to watch</h2>
            {hurt.length === 0 && notes.length === 0 ? (
              <p className="text-sm text-stone-500">No risk flags were stored for this plot.</p>
            ) : (
              <ul className="space-y-2">
                {hurt.slice(0, 6).map((rc, i) => (
                  <ReasonCard key={`h-${i}`} rc={rc} />
                ))}
                {notes.slice(0, 4).map((rc, i) => (
                  <ReasonCard key={`n-${i}`} rc={rc} />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {(pos.length > 0 || neg.length > 0) && (
        <div className="bg-white rounded-xl border border-rule p-5">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Drivers</p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-3">What added, and what took away</h2>
          <div className="grid sm:grid-cols-2 gap-4">
            <div>
              <p className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider mb-2">Added</p>
              {pos.length === 0 ? (
                <p className="text-sm text-stone-500">None listed.</p>
              ) : (
                <ul className="space-y-3">
                  {pos.slice(0, 5).map((d, i) => (
                    <DriverRow
                      key={i}
                      label={d.label || humanizeKey(d.feature || '')}
                      contribution={d.contribution}
                      maxAbs={maxAbs}
                      positive
                    />
                  ))}
                </ul>
              )}
            </div>
            <div>
              <p className="text-[11px] font-semibold text-amber-800 uppercase tracking-wider mb-2">Took away</p>
              {neg.length === 0 ? (
                <p className="text-sm text-stone-500">None listed.</p>
              ) : (
                <ul className="space-y-3">
                  {neg.slice(0, 5).map((d, i) => (
                    <DriverRow
                      key={i}
                      label={d.label || humanizeKey(d.feature || '')}
                      contribution={d.contribution}
                      maxAbs={maxAbs}
                      positive={false}
                    />
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      {scenarios.length > 0 && (
        <div className="bg-white rounded-xl border border-rule p-5">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">If things improved</p>
          <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-1">What would raise the score</h2>
          {cf?.current_score != null && cf.projected_score_all_improvements != null && (
            <p className="text-[13px] text-stone-500 mb-3">
              From {formatNumber(cf.current_score, 0)} toward {formatNumber(cf.projected_score_all_improvements, 0)} if
              the steps below all landed.
            </p>
          )}
          <ul className="grid sm:grid-cols-2 gap-3">
            {scenarios.map((s, i) => (
              <li key={s.id ?? i} className="rounded-lg border border-rule bg-paper/50 p-4">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-semibold text-stone-900">{s.title || 'Improvement'}</p>
                  {s.score_gain != null && (
                    <span className="text-[11px] font-bold px-2 py-0.5 rounded-full border bg-emerald-50 border-emerald-200 text-emerald-800 shrink-0">
                      +{formatNumber(s.score_gain, 0)}
                    </span>
                  )}
                </div>
                {s.action && <p className="text-[13px] text-stone-600 mt-2 leading-snug">{s.action}</p>}
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {s.component && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full border bg-white border-rule text-stone-600">
                      {subIndexLabel(s.component)}
                    </span>
                  )}
                  {prettyFeasibility(s.feasibility) && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full border bg-white border-rule text-stone-600">
                      {prettyFeasibility(s.feasibility)}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {roadmapSteps.length > 0 && (
            <ol className="mt-4 space-y-2">
              {roadmapSteps.map((step, i) => (
                <li key={i} className="flex gap-3 text-[13px] text-stone-600 leading-snug">
                  <span className="text-[11px] font-bold text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-full w-5 h-5 flex items-center justify-center shrink-0 mt-0.5">
                    {step.step ?? i + 1}
                  </span>
                  <span>
                    {step.action || '—'}
                    {step.timeframe ? <span className="text-stone-400"> · {step.timeframe}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {(showEnglish || showTranslated) && (
        <div className="grid lg:grid-cols-2 gap-4">
          {showEnglish && (
            <div className="bg-white rounded-xl border border-rule p-5">
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">In short</p>
              <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-2">English summary</h2>
              <p className="text-[13px] text-stone-600 leading-relaxed whitespace-pre-wrap">{english}</p>
            </div>
          )}
          {showTranslated && (
            <div className="bg-white rounded-xl border border-rule p-5">
              <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">
                {langName || 'Translation'}
              </p>
              <h2 className="text-sm font-bold text-stone-900 mt-0.5 mb-2">Same summary, in {langName || 'translation'}</h2>
              <p className="text-[13px] text-stone-600 leading-relaxed whitespace-pre-wrap">{translated}</p>
            </div>
          )}
        </div>
      )}

      {reasons.length === 0 && !showEnglish && pos.length === 0 && scenarios.length === 0 && (
        <div className="bg-white rounded-xl border border-rule p-6">
          <p className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">Explainability</p>
          <p className="text-sm text-stone-500 mt-2">
            No explanation notes are stored for this plot yet. Re-run the assessment to generate them.
          </p>
        </div>
      )}
    </div>
  );
}
