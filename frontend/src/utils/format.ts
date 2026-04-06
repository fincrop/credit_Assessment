export function formatNumber(n: unknown, digits = 1): string {
  if (n === null || n === undefined || n === "") return "—";
  const x = typeof n === "number" ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return "—";
  return x.toFixed(digits);
}

export function formatPct(n: unknown): string {
  if (n === null || n === undefined) return "—";
  if (typeof n === "string" && n.includes("%")) return n;
  const x = typeof n === "number" ? n : parseFloat(String(n));
  if (Number.isNaN(x)) return String(n);
  return `${x.toFixed(1)}%`;
}

export function humanizeKey(key: string): string {
  const map: Record<string, string> = {
    crop_detection: "Crop detection",
    crop_performance: "Crop performance",
    yield_potential: "Yield potential",
    weather_safety: "Weather safety",
    anomaly_penalty: "Anomaly / stress",
    cropping_intensity: "Cropping intensity",
    govt_benefits: "Govt. benefits",
  };
  return (
    map[key] ||
    key
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

export function riskBadgeClass(risk: string | undefined): string {
  const r = (risk || "").toUpperCase();
  if (r === "LOW") return "risk-low";
  if (r === "MEDIUM") return "risk-medium";
  if (r === "HIGH") return "risk-high";
  if (r.includes("VERY")) return "risk-very-high";
  return "risk-unknown";
}
