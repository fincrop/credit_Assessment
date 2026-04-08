import { useCallback, useRef, useState } from "react";
import { runAssessment } from "./api/client";
import type { AssessmentPayload } from "./types/assessment";
import { AssessForm } from "./components/AssessForm";
import { ErrorBanner } from "./components/ErrorBanner";
import { Header } from "./components/Header";
import { ResultView } from "./components/ResultView";

const LOADING_STEPS = [
  "Connecting to satellite and farm services",
  "Downloading historical data for Year 1",
  "Downloading historical data for Year 2",
  "Downloading historical data for Year 3",
  "Detecting crop intervals and vegetation trends",
  "Evaluating weather events per interval",
  "Computing performance and credit components",
  "Finalizing assessment report",
];

export default function App() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AssessmentPayload | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [stepIdx, setStepIdx] = useState(0);
  const tickRef = useRef<number | null>(null);

  const onRun = useCallback(
    async (p: {
      farmerId: string;
      includeHeavy: boolean;
      pmKisanEnrolled: boolean;
      hasCropInsurance: boolean;
    }) => {
      setError(null);
      setResult(null);
      setLoading(true);
      setElapsed(0);
      setStepIdx(0);
      const t0 = Date.now();
      tickRef.current = window.setInterval(() => {
        const sec = Math.floor((Date.now() - t0) / 1000);
        setElapsed(sec);
        setStepIdx(Math.min(LOADING_STEPS.length - 1, Math.floor(sec / 18)));
      }, 1000);

      try {
        const data = await runAssessment({
          farmerId: p.farmerId,
          includeHeavy: p.includeHeavy,
          pmKisanEnrolled: p.pmKisanEnrolled,
          hasCropInsurance: p.hasCropInsurance,
        });
        setResult(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (tickRef.current) clearInterval(tickRef.current);
        tickRef.current = null;
        setLoading(false);
      }
    },
    [],
  );

  return (
    <div className="app-shell">
      <Header />
      {!result && <AssessForm onSubmit={onRun} loading={loading} />}

      {loading && (
        <div className="loading-overlay loading-hero">
          <div className="spinner pulse" aria-hidden />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, color: "var(--text)" }}>
              {LOADING_STEPS[stepIdx]}
            </div>
            <div style={{ marginTop: "0.3rem" }}>
              Processing satellite + weather + scoring pipeline… <strong>{elapsed}s</strong>
            </div>
            <div className="step-track">
              {LOADING_STEPS.map((s, idx) => (
                <div key={s} className={`step-dot ${idx <= stepIdx ? "done" : ""}`} />
              ))}
            </div>
          </div>
        </div>
      )}

      {error && <ErrorBanner message={error} />}

      {result && !loading && <ResultView data={result} onBack={() => setResult(null)} />}

      <footer style={{ marginTop: "2rem", fontSize: "0.75rem", color: "var(--text-muted)" }}>
        Agri Credit Risk Dashboard
      </footer>
    </div>
  );
}
