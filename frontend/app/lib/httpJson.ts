/**
 * Safe fetch response parsing — avoids "Unexpected token '<'" when a route
 * returns HTML (404 page, proxy error, stale deployment).
 */

export type JsonBody<T> = {
  status: number;
  ok: boolean;
  data: T | null;
  text: string;
  isJson: boolean;
};

export async function readJsonBody<T = Record<string, unknown>>(
  res: Response
): Promise<JsonBody<T>> {
  const text = await res.text();
  const trimmed = text.trim();
  if (!trimmed) {
    return { status: res.status, ok: res.ok, data: null, text, isJson: true };
  }
  try {
    return {
      status: res.status,
      ok: res.ok,
      data: JSON.parse(text) as T,
      text,
      isJson: true,
    };
  } catch {
    return { status: res.status, ok: false, data: null, text, isJson: false };
  }
}

function errorFromData(data: Record<string, unknown> | null): string | null {
  if (!data) return null;
  const err = data.error;
  if (typeof err === 'string' && err.trim()) return err;
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  if (typeof data.detail === 'string' && data.detail.trim()) return data.detail;
  return null;
}

/** User-facing message when the server did not return JSON. */
export function nonJsonApiMessage(
  status: number,
  text: string,
  context: string
): string {
  const isHtml = /^\s*</.test(text);
  if (status === 404 && isHtml) {
    return `${context}: API route not found (HTTP 404). The frontend may be on an old deployment — redeploy and try again.`;
  }
  if (isHtml) {
    return `${context}: server returned an HTML error page (HTTP ${status}) instead of JSON. Check deployment logs or retry in a moment.`;
  }
  const snippet = text.replace(/\s+/g, ' ').slice(0, 160);
  return `${context}: invalid response (HTTP ${status})${snippet ? `: ${snippet}` : ''}`;
}

export function apiErrorMessage(
  parsed: JsonBody<Record<string, unknown> | null>,
  fallback: string,
  context?: string
): string {
  if (!parsed.isJson) {
    return nonJsonApiMessage(parsed.status, parsed.text, context || fallback);
  }
  return (
    errorFromData(parsed.data) ||
    (parsed.status === 404
      ? `${context || fallback}: not found (HTTP 404)`
      : `${fallback} (HTTP ${parsed.status})`)
  );
}
