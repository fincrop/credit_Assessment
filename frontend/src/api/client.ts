import type { AssessmentPayload } from "../types/assessment";

function normalizeBase(url: string): string {
  return url.replace(/\/+$/, "");
}

export async function runAssessment(params: {
  farmerId: string;
  includeHeavy?: boolean;
  pmKisanEnrolled?: boolean;
  hasCropInsurance?: boolean;
}): Promise<AssessmentPayload> {
  const base = defaultApiBase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  const res = await fetch(`${base}/v1/assess`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      farmer_id: params.farmerId.trim(),
      include_heavy: params.includeHeavy ?? false,
      pm_kisan_enrolled: params.pmKisanEnrolled ?? false,
      has_crop_insurance: params.hasCropInsurance ?? false,
    }),
  });

  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Server returned non-JSON (${res.status}): ${text.slice(0, 200)}`);
  }

  if (!res.ok) {
    const msg =
      typeof data === "object" && data !== null && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new Error(msg);
  }

  return data as AssessmentPayload;
}

export function defaultApiBase(): string {
  const v = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (v?.trim()) return normalizeBase(v.trim());
  if (import.meta.env.DEV) return "http://127.0.0.1:8000";
  return "https://credit-assessment-43t0.onrender.com";
}
