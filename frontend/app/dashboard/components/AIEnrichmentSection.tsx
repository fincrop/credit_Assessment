'use client';

import type { AssessmentPayload, AiCounterfactuals } from '../../types/assessment';
import { formatNumber, humanizeKey } from '../../lib/format';
import { useRiskView } from '../../lib/useRiskView';
import { polarityIcon, polarityBgClass } from '../../lib/formatRisk';

function DriverList({
  title,
  items,
  sign,
}: {
  title: string;
  items: { feature?: string; label?: string; contribution?: number; value?: number }[];
  sign: '+' | '−';
}) {
  const isPositive = sign === '+';
  return (
    <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-4">
      <p
        className={`text-[11px] font-semibold uppercase tracking-wider mb-3 ${
          isPositive ? 'text-emerald-700' : 'text-red-600'
        }`}
      >
        {title}
      </p>
      {items.length === 0 ? (
        <p className="text-xs text-stone-500">No drivers listed.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((d, i) => (
            <li key={i} className="flex items-center justify-between gap-3">
              <span className="text-xs text-stone-600 truncate">
                {d.label ?? humanizeKey(d.feature ?? '')}
              </span>
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {d.contribution != null && (
                  <span
                    className={`font-mono text-xs font-semibold ${
                      isPositive ? 'text-emerald-700' : 'text-red-600'
                    }`}
                  >
                    {sign}
                    {formatNumber(Math.abs(d.contribution), 3)}
                  </span>
                )}
                {d.value != null && (
                  <span className="text-[10px] text-stone-400">({formatNumber(d.value)})</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AIEnrichmentSection({ data }: { data: AssessmentPayload }) {
  const ai = data.ai_enrichment;
  const view = useRiskView(data);

  if (!ai) {
    return (
      <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 space-y-4">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
          AI &amp; Explainability
        </h2>
        <p className="text-sm text-stone-500">
          No ai_enrichment block. Index reason codes (if any) still appear below.
        </p>
        {view.reasonCodes.length > 0 && (
          <ul className="space-y-2">
            {view.reasonCodes.slice(0, 8).map((rc, i) => (
              <li
                key={i}
                className={`flex gap-2 text-xs rounded-lg border px-3 py-2 ${polarityBgClass(rc.polarity)}`}
              >
                <span className="font-mono shrink-0">{polarityIcon(rc.polarity)}</span>
                <span>
                  {rc.code && (
                    <span className="font-mono text-[10px] opacity-70 mr-1.5">{rc.code}</span>
                  )}
                  {rc.message || rc.code || '—'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const ex = ai.explainability ?? ai.explainability_mongo;
  // Prefer full counterfactuals (includes action + list roadmap); mongo is slim fallback
  const cfRaw = (ai.counterfactuals ?? ai.counterfactuals_mongo) as
    | AiCounterfactuals
    | undefined;
  const cf = cfRaw;

  const narrative = ai.english_narrative;
  const translated = ai.translated_narrative;

  const roadmap = cf?.improvement_roadmap;
  const roadmapSteps = Array.isArray(roadmap)
    ? roadmap
    : typeof roadmap === 'string' && roadmap.trim()
      ? [{ action: roadmap }]
      : [];

  return (
    <div className="bg-white rounded-xl border border-[#E4DFD4] p-6 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider">
          AI &amp; Explainability
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`text-xs px-2.5 py-1 rounded-full border ${
              ai.groq_used
                ? 'bg-sky-50 border-sky-200 text-sky-800'
                : 'bg-stone-100 border-stone-200 text-stone-600'
            }`}
          >
            Groq: {ai.groq_used ? 'used' : `skipped${ai.groq_skipped_reason ? ` — ${ai.groq_skipped_reason}` : ''}`}
          </span>
          {ai.narrative_source && (
            <span className="text-xs px-2.5 py-1 rounded-full border bg-[#F5F2EB] border-[#E4DFD4] text-stone-600 font-mono">
              narrative: {ai.narrative_source}
            </span>
          )}
          {ai.translation_language && (
            <span className="text-xs px-2.5 py-1 rounded-full border bg-violet-50 border-violet-200 text-violet-800">
              {ai.translation_language}
            </span>
          )}
        </div>
      </div>

      <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-4">
        <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
          Narrative (English)
        </p>
        {narrative ? (
          <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap">{narrative}</p>
        ) : (
          <p className="text-sm text-stone-500">
            Narrative missing — expected deterministic fallback from the pipeline.
          </p>
        )}
      </div>

      {translated && (
        <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-4">
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Narrative (Translated)
          </p>
          <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap">{translated}</p>
        </div>
      )}

      {view.reasonCodes.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Reason codes
          </p>
          <ul className="space-y-2">
            {view.reasonCodes.slice(0, 8).map((rc, i) => (
              <li
                key={i}
                className={`flex gap-2 text-xs rounded-lg border px-3 py-2 ${polarityBgClass(rc.polarity)}`}
              >
                <span className="font-mono shrink-0">{polarityIcon(rc.polarity)}</span>
                <span>
                  {rc.code && (
                    <span className="font-mono text-[10px] opacity-70 mr-1.5">{rc.code}</span>
                  )}
                  {rc.message || rc.code || '—'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ex ? (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider">
              Feature Drivers ({ex.method ?? 'attribution'})
            </p>
            {ex.shap_available && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-50 border border-indigo-200 text-indigo-700">
                SHAP
              </span>
            )}
          </div>
          {ex.credit_summary && (
            <p className="text-xs text-stone-500 mb-3 leading-relaxed">{ex.credit_summary}</p>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <DriverList
              title="Positive Drivers"
              items={ex.top_positive_drivers ?? []}
              sign="+"
            />
            <DriverList
              title="Negative Drivers"
              items={ex.top_negative_drivers ?? []}
              sign="−"
            />
          </div>
        </div>
      ) : (
        <p className="text-xs text-stone-500">SHAP / explainability block not present.</p>
      )}

      {cf && (cf.scenarios?.length ?? 0) > 0 ? (
        <div>
          <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
            Counterfactual Scenarios
          </p>
          <div className="bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-4 mb-3">
            <div className="flex items-center gap-4">
              <div className="text-center">
                <p className="text-xs text-stone-400 mb-1">Current Score</p>
                <p className="text-xl font-bold text-stone-800">{formatNumber(cf.current_score)}</p>
              </div>
              <div className="flex-1 h-px bg-[#E4DFD4] relative">
                <span className="absolute inset-0 flex items-center justify-center text-emerald-600 text-sm">
                  →
                </span>
              </div>
              <div className="text-center">
                <p className="text-xs text-stone-400 mb-1">Projected (all)</p>
                <p className="text-xl font-bold text-emerald-700">
                  {formatNumber(cf.projected_score_all_improvements)}
                </p>
              </div>
            </div>
          </div>
          <ul className="space-y-2">
            {(cf.scenarios ?? []).map((s, i) => (
              <li
                key={s.id ?? i}
                className="flex items-start gap-3 bg-[#F5F2EB] rounded-lg border border-[#E4DFD4] p-3"
              >
                <span className="text-emerald-700 font-bold text-sm flex-shrink-0 mt-0.5">
                  +{formatNumber(s.score_gain)} pts
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-stone-800">{s.title}</p>
                  {s.action && (
                    <p className="text-xs text-stone-500 mt-1 leading-relaxed">{s.action}</p>
                  )}
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    {s.component && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-white border border-[#E4DFD4] text-stone-500 font-mono">
                        {s.component}
                      </span>
                    )}
                    {s.feasibility && (
                      <span className="text-[10px] text-stone-500">{s.feasibility}</span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {roadmapSteps.length > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold text-stone-400 uppercase tracking-wider mb-2">
                Improvement roadmap
              </p>
              <ol className="space-y-1.5 list-decimal list-inside">
                {roadmapSteps.map((step, i) => (
                  <li key={i} className="text-xs text-stone-600 leading-relaxed">
                    {step.action || '—'}
                    {(step.timeframe || step.score_gain != null) && (
                      <span className="text-stone-400 ml-1">
                        (
                        {[
                          step.timeframe,
                          step.score_gain != null
                            ? `+${formatNumber(step.score_gain)} pts`
                            : null,
                          step.feasibility,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                        )
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-stone-500">No counterfactual scenarios in this payload.</p>
      )}
    </div>
  );
}
