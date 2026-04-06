import type { AssessmentPayload } from "../types/assessment";
import { formatNumber } from "../utils/format";

export function LocationStrip({ data }: { data: AssessmentPayload }) {
  const loc = data.location;
  const benefits = data.farmer_benefits;

  return (
    <div className="card">
      <h2>Farm context</h2>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "1rem",
          alignItems: "center",
          fontSize: "0.88rem",
        }}
      >
        <span>
          <strong>Farmer</strong> {data.farmer_id ?? "—"}
        </span>
        {loc?.latitude != null && loc?.longitude != null && (
          <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
            {formatNumber(loc.latitude, 4)}°, {formatNumber(loc.longitude, 4)}°
          </span>
        )}
        {loc?.region && (
          <span className="tag">{String(loc.region)}</span>
        )}
        {data.field_area_ha != null && (
          <span>
            <strong>Area</strong> {formatNumber(data.field_area_ha, 2)} ha
          </span>
        )}
        {data.assessment_date && (
          <span style={{ color: "var(--text-muted)" }}>Assessed {data.assessment_date}</span>
        )}
      </div>
      {benefits && (
        <p style={{ margin: "0.75rem 0 0", fontSize: "0.82rem", color: "var(--text-muted)" }}>
          PM-KISAN: {benefits.pm_kisan_enrolled ? "yes" : "no"} · Crop insurance:{" "}
          {benefits.has_crop_insurance ? "yes" : "no"}
        </p>
      )}
      {data.crop_hint && (
        <p style={{ margin: "0.35rem 0 0", fontSize: "0.82rem" }}>
          <strong>DB crop hint:</strong> {data.crop_hint}
        </p>
      )}
      {data.sowing_date && (
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.82rem" }}>
          <strong>Sowing (DB):</strong> {data.sowing_date}
        </p>
      )}
    </div>
  );
}
