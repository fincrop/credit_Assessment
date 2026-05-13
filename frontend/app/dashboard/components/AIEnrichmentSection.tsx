import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber, humanizeKey } from '../../lib/format';

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
    <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
      <p className={`text-[11px] font-semibold uppercase tracking-wider mb-3 ${isPositive ? 'text-emerald-600' : 'text-red-600'}`}>{title}</p>
      {items.length === 0 ? (
        <p className="text-xs text-gray-700">—</p>
      ) : (
        <ul className="space-y-2">
          {items.map((d, i) => (
            <li key={i} className="flex items-center justify-between gap-3">
              <span className="text-xs text-gray-400 truncate">{d.label ?? humanizeKey(d.feature ?? '')}</span>
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {d.contribution != null && (
                  <span className={`font-mono text-xs font-semibold ${isPositive ? 'text-emerald-400' : 'text-red-400'}`}>
                    {sign}{formatNumber(Math.abs(d.contribution), 3)}
                  </span>
                )}
                {d.value != null && (
                  <span className="text-[10px] text-gray-700">({formatNumber(d.value)})</span>
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

  if (!ai) {
    return (
      <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">AI &amp; Explainability</h2>
        <div className="flex items-center gap-3 p-4 rounded-lg bg-[#0d1117] border border-[#30363d]">
          <span className="text-2xl">🤖</span>
          <div>
            <p className="text-sm font-medium text-gray-400">AI Enrichment Not Run</p>
            <p className="text-xs text-gray-600 mt-0.5">Stage 12 (Groq/Sarvam/SHAP) was not invoked in this pipeline run. Configure GROQ_API_KEY to enable.</p>
          </div>
        </div>
      </div>
    );
  }

  const ex = ai.explainability ?? ai.explainability_mongo;
  const cf = (ai.counterfactuals_mongo ?? ai.counterfactuals) as {
    current_score?: number;
    projected_score_all_improvements?: number;
    scenarios?: { id?: string; title?: string; score_gain?: number; component?: string; feasibility?: string }[];
    improvement_roadmap?: string;
  } | undefined;

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">AI &amp; Explainability</h2>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2.5 py-1 rounded-full border ${ai.groq_used ? 'bg-blue-500/10 border-blue-500/20 text-blue-400' : 'bg-gray-700/50 border-gray-600 text-gray-500'}`}>
            Groq: {ai.groq_used ? 'used' : `skipped${ai.groq_skipped_reason ? ` — ${ai.groq_skipped_reason}` : ''}`}
          </span>
          {ai.translation_language && (
            <span className="text-xs px-2.5 py-1 rounded-full border bg-purple-500/10 border-purple-500/20 text-purple-400">
              🌐 {ai.translation_language}
            </span>
          )}
        </div>
      </div>

      {/* Narratives */}
      {(ai.english_preview || ai.english_narrative) && (
        <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Narrative (English)</p>
          <p className="text-sm text-gray-400 leading-relaxed">{ai.english_preview ?? ai.english_narrative}</p>
        </div>
      )}

      {(ai.translated_preview || ai.translated_narrative) && (
        <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4">
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Narrative (Translated)</p>
          <p className="text-sm text-gray-400 leading-relaxed">{ai.translated_preview ?? ai.translated_narrative}</p>
        </div>
      )}

      {/* Explainability drivers */}
      {ex && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider">
              Feature Drivers ({ex.method ?? 'attribution'})
            </p>
            {ex.shap_available && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">SHAP</span>
            )}
          </div>
          {ex.credit_summary && (
            <p className="text-xs text-gray-600 mb-3 leading-relaxed">{ex.credit_summary}</p>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <DriverList title="Positive Drivers" items={ex.top_positive_drivers ?? []} sign="+" />
            <DriverList title="Negative Drivers" items={ex.top_negative_drivers ?? []} sign="−" />
          </div>
        </div>
      )}

      {/* Counterfactuals */}
      {cf && (cf.scenarios?.length ?? 0) > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider mb-2">Counterfactual Scenarios</p>
          <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-4 mb-3">
            <div className="flex items-center gap-4">
              <div className="text-center">
                <p className="text-xs text-gray-600 mb-1">Current Score</p>
                <p className="text-xl font-bold text-gray-300">{formatNumber(cf.current_score)}</p>
              </div>
              <div className="flex-1 h-px bg-[#30363d] relative">
                <span className="absolute inset-0 flex items-center justify-center text-emerald-500 text-sm">→</span>
              </div>
              <div className="text-center">
                <p className="text-xs text-gray-600 mb-1">Projected (all)</p>
                <p className="text-xl font-bold text-emerald-400">{formatNumber(cf.projected_score_all_improvements)}</p>
              </div>
            </div>
          </div>
          <ul className="space-y-2">
            {(cf.scenarios ?? []).map((s, i) => (
              <li key={s.id ?? i} className="flex items-start gap-3 bg-[#0d1117] rounded-lg border border-[#30363d] p-3">
                <span className="text-emerald-500 font-bold text-sm flex-shrink-0 mt-0.5">+{formatNumber(s.score_gain)} pts</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-300">{s.title}</p>
                  <div className="flex items-center gap-2 mt-1">
                    {s.component && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#21262d] border border-[#30363d] text-gray-500 font-mono">{s.component}</span>
                    )}
                    {s.feasibility && (
                      <span className="text-[10px] text-gray-700">{s.feasibility}</span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {cf.improvement_roadmap && (
            <p className="text-xs text-gray-600 mt-3 leading-relaxed">{cf.improvement_roadmap}</p>
          )}
        </div>
      )}
    </div>
  );
}
