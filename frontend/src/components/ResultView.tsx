import { useMemo, useState } from "react";
import type { AssessmentPayload } from "../types/assessment";
import { AIEnrichmentSection } from "./AIEnrichmentSection";
import { ComponentScores } from "./ComponentScores";
import { CropCyclesSection } from "./CropCyclesSection";
import { CroppingSection } from "./CroppingSection";
import { JsonInspector } from "./JsonInspector";
import { LocationStrip } from "./LocationStrip";
import { PerformanceSection } from "./PerformanceSection";
import { SummaryHero } from "./SummaryHero";
import { WeatherSection } from "./WeatherSection";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "scores", label: "Components" },
  { id: "crops", label: "Cropping" },
  { id: "performance", label: "Performance" },
  { id: "weather", label: "Weather" },
  { id: "cycles", label: "Cycles" },
  { id: "ai", label: "AI / Explain" },
  { id: "raw", label: "Raw JSON" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function ResultView({ data }: { data: AssessmentPayload }) {
  const [tab, setTab] = useState<TabId>("overview");

  const failed = data.status !== "SUCCESS";

  const warnings = useMemo(() => {
    const w = [...(data.warnings ?? [])];
    if (failed && data.error) w.unshift(data.error);
    return w;
  }, [data, failed]);

  return (
    <div>
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
        </>
      )}

      {tab === "scores" && <ComponentScores credit={data.credit_assessment} />}

      {tab === "crops" && <CroppingSection data={data} />}

      {tab === "performance" && <PerformanceSection data={data} />}

      {tab === "weather" && <WeatherSection data={data} />}

      {tab === "cycles" && <CropCyclesSection data={data} />}

      {tab === "ai" && <AIEnrichmentSection data={data} />}

      {tab === "raw" && <JsonInspector data={data} />}

      {tab !== "raw" && (
        <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "1rem" }}>
          Tip: use the <strong>Raw JSON</strong> tab to copy the full API response for MongoDB-aligned
          ETL or audit.
        </p>
      )}
    </div>
  );
}
