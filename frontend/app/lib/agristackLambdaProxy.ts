/**
 * Optional Mumbai Lambda proxy for AgriStack (Token / Seek / KDSS).
 * When AGRISTACK_PROXY_URL is unset, callers should hit AgriStack directly
 * (fine for local India egress).
 */

function cleanBaseUrl(raw: string | undefined): string {
  const s = (raw || '').trim();
  if (!s) return '';
  const md = s.match(/\((https?:\/\/[^)\s]+)\)/);
  if (md) return md[1].replace(/\/+$/, '');
  const plain = s.match(/https?:\/\/[^\s\]]+/);
  return (plain ? plain[0] : s).replace(/\/+$/, '');
}

export function agristackProxyBase(): string | null {
  const base = cleanBaseUrl(process.env.AGRISTACK_PROXY_URL);
  return base || null;
}

export function agristackProxyEnabled(): boolean {
  return Boolean(agristackProxyBase());
}

/** Default public webhook bases for Seek sender_uri when using Lambda. */
export function agristackLambdaWebhookUrls() {
  const base = agristackProxyBase();
  if (!base) return null;
  return {
    farmers: `${base}/webhook/farmers/on-seek`,
    legacy: `${base}/webhook/on-seek`,
    kdss: `${base}/webhook/kdss/on-seek`,
  };
}

type ProxyAction =
  | 'token'
  | 'seek'
  | 'farmer_seek'
  | 'kdss_seek'
  | 'krishi_dss_seek'
  | 'token_and_seek';

export async function callAgristackLambda(
  action: ProxyAction,
  payload: Record<string, unknown>
): Promise<{
  ok: boolean;
  status: number;
  json: Record<string, unknown>;
  responseTime: number;
}> {
  const base = agristackProxyBase();
  if (!base) {
    throw new Error('AGRISTACK_PROXY_URL is not set');
  }

  const secret = (
    process.env.AGRISTACK_PROXY_SECRET ||
    process.env.PROXY_SECRET ||
    ''
  ).trim();

  const started = Date.now();
  const res = await fetch(`${base}/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(secret ? { 'x-agristack-proxy-secret': secret } : {}),
    },
    body: JSON.stringify({ action, ...payload }),
  });

  const responseTime = Date.now() - started;
  let json: Record<string, unknown>;
  try {
    json = (await res.json()) as Record<string, unknown>;
  } catch {
    json = {
      success: false,
      error: { message: 'Lambda returned non-JSON' },
    };
  }

  return { ok: res.ok, status: res.status, json, responseTime };
}
