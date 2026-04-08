import { useMemo, useState } from "react";
import type { AssessmentPayload } from "../types/assessment";
import { AIEnrichmentSection } from "./AIEnrichmentSection";
import { CropCyclesSection } from "./CropCyclesSection";
import { CroppingSection } from "./CroppingSection";
import { LocationStrip } from "./LocationStrip";
import { PerformanceSection } from "./PerformanceSection";
import { SummaryHero } from "./SummaryHero";
import { WeatherSection } from "./WeatherSection";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "cropPerf", label: "Crop & Performance" },
  { id: "weather", label: "Weather" },
  { id: "cycles", label: "Cycles" },
  { id: "ai", label: "Explainability" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function ResultView({ data, onBack }: { data: AssessmentPayload; onBack: () => void }) {
  const [tab, setTab] = useState<TabId>("overview");

  const failed = data.status !== "SUCCESS";

  const warnings = useMemo(() => {
    const w = [...(data.warnings ?? [])];
    if (failed && data.error) w.unshift(data.error);
    return w;
  }, [data, failed]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.75rem" }}>
        <h2 style={{ margin: 0 }}>Assessment Insights</h2>
        <button className="btn" type="button" onClick={onBack}>
          New assessment
        </button>
      </div>
      {warnings.length > 0 && (
        <div
          className="card"
          style={{
            borderColor: "rgba(232, 184, 74, 0.45)",
            background: "rgba(232, 184, 74, 0.08)",
          }}
        >
          <h2 style={{ color: "var(--warn)" }}>Notices</h2>
          <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.88rem" }}>
            {warnings.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      <nav className="nav-tabs" aria-label="Report sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <>
          <LocationStrip data={data} />
          <SummaryHero data={data} />
          <CropCyclesSection data={data} />
        </>
      )}

      {tab === "cropPerf" && (
        <>
          <CroppingSection data={data} />
          <PerformanceSection data={data} />
        </>
      )}

      {tab === "weather" && <WeatherSection data={data} />}

      {tab === "cycles" && <CropCyclesSection data={data} />}

      {tab === "ai" && <AIEnrichmentSection data={data} />}
    </div>
  );
}
