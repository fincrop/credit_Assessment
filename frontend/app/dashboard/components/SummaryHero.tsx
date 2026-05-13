import type { AssessmentPayload } from '../../types/assessment';
import { formatNumber, riskBgClass, humanizeKey } from '../../lib/format';

function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-[#0d1117] rounded-lg border border-[#30363d] p-3.5">
      <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-1.5">{label}</p>
      <p className="font-semibold text-gray-200 text-sm">{value}</p>
    </div>
  );
}

export function SummaryHero({ data }: { data: AssessmentPayload }) {
  const ca = data.credit_assessment;
  const score = ca?.credit_score ?? (data.summary?.credit_score as number | undefined);
  const risk = ca?.risk_category ?? (data.summary?.risk_category as string | undefined);
  const pct = typeof score === 'number' ? Math.min(100, Math.max(0, score)) : 0;
  const method = ca?.method ?? (typeof data.summary?.scoring_method === 'string' ? data.summary.scoring_method : undefined);
  const narrative = ca?.scoring_narrative;

  const scoreColor =
    pct >= 70 ? '#22c55e' : pct >= 45 ? '#f59e0b' : '#ef4444';

  // SVG donut gauge
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const dash = (pct / 100) * circumference;

  return (
    <div className="bg-[#161b22] rounded-xl border border-[#30363d] p-6">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-5">Credit Outcome</h2>

      <div className="flex flex-wrap gap-6 items-center">
        {/* Score donut */}
        <div className="relative flex-shrink-0">
          <svg width="130" height="130" viewBox="0 0 130 130">
            <circle cx="65" cy="65" r={radius} fill="none" stroke="#21262d" strokeWidth="10" />
            <circle
              cx="65" cy="65" r={radius}
              fill="none"
              stroke={scoreColor}
              strokeWidth="10"
              strokeDasharray={`${dash} ${circumference}`}
              strokeDashoffset={circumference * 0.25}
              strokeLinecap="round"
              transform="rotate(-90 65 65)"
              style={{ transition: 'stroke-dasharray 0.8s ease' }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-2xl font-bold" style={{ color: scoreColor }}>
              {formatNumber(score, 1)}
            </span>
            <span className="text-[10px] text-gray-600 font-medium uppercase tracking-widest">Score</span>
          </div>
        </div>

        {/* Meta info */}
        <div className="flex-1 min-w-[200px] space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            {risk && (
              <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold border ${riskBgClass(typeof risk === 'string' ? risk : undefined)}`}>
                {typeof risk === 'string' ? risk : '—'}
              </span>
            )}
            {method && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-[#21262d] border border-[#30363d] text-[11px] text-gray-500 font-mono">
                {method}
              </span>
            )}
            {ca?.ml_components_silenced && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-500/10 border border-amber-500/20 text-[11px] text-amber-500">
                ML silenced
              </span>
            )}
          </div>
          <p className="text-sm text-gray-400">
            Assessment quality: <strong className="text-gray-200">{score != null ? formatNumber(score, 1) : '—'} / 100</strong>
          </p>
          {data.processing_time_seconds != null && (
            <p className="text-[11px] text-gray-600">
              Processed in {formatNumber(data.processing_time_seconds, 1)}s · {(data.pipeline_stages ?? []).length} pipeline stages
            </p>
          )}
        </div>
      </div>

      {/* Component scores */}
      {ca?.component_scores && (
        <div className="mt-5 pt-5 border-t border-[#30363d]">
          <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-widest mb-3">Component Scores</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {Object.entries(ca.component_scores).map(([k, v]) => (
              <div key={k}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-500">{humanizeKey(k)}</span>
                  <span className="text-gray-300 font-mono">{formatNumber(v, 1)}</span>
                </div>
                <div className="h-1.5 bg-[#21262d] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${Math.max(0, Math.min(100, v ?? 0))}%`,
                      background: v > 65 ? '#22c55e' : v > 40 ? '#f59e0b' : '#ef4444',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Narrative */}
      {narrative && (
        <p className="mt-4 text-[12px] text-gray-600 leading-relaxed border-t border-[#30363d] pt-4">
          {narrative}
        </p>
      )}
    </div>
  );
}
