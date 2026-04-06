import { useCallback, useRef, useState } from "react";
import { runAssessment } from "./api/client";
import type { AssessmentPayload } from "./types/assessment";
import { AssessForm } from "./components/AssessForm";
import { ErrorBanner } from "./components/ErrorBanner";
import { Header } from "./components/Header";
import { ResultView } from "./components/ResultView";

export default function App() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AssessmentPayload | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const tickRef = useRef<number | null>(null);

  const onRun = useCallback(
    async (p: { baseUrl: string; farmerId: string; apiKey: string; includeHeavy: boolean }) => {
      setError(null);
      setResult(null);
      setLoading(true);
      setElapsed(0);
      const t0 = Date.now();
      tickRef.current = window.setInterval(() => {
        setElapsed(Math.floor((Date.now() - t0) / 1000));
      }, 1000);

      try {
        const data = await runAssessment({
          baseUrl: p.baseUrl,
          farmerId: p.farmerId,
          apiKey: p.apiKey,
          includeHeavy: p.includeHeavy,
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
      <AssessForm onSubmit={onRun} loading={loading} />

      {loading && (
        <div className="loading-overlay">
          <div className="spinner" aria-hidden />
          <div>
            Running satellite credit pipeline… <strong>{elapsed}s</strong> elapsed.
          </div>
        </div>
      )}

      {error && <ErrorBanner message={error} />}

      {result && !loading && <ResultView data={result} />}

      <footer style={{ marginTop: "2rem", fontSize: "0.75rem", color: "var(--text-muted)" }}>
        Agri Credit Risk Dashboard — standalone UI. Configure CORS on the FastAPI service for your
        deployed origin. See repo <code>DEPLOYMENT_AND_FRONTEND.md</code>.
      </footer>
    </div>
  );
}
