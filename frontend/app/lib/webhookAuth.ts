import { NextRequest, NextResponse } from 'next/server';

/**
 * Shared-secret gate for AgriStack webhook POSTs.
 * - Production: WEBHOOK_SECRET is mandatory; missing secret → 401.
 * - Non-production: if unset, requests are allowed (local/dev convenience).
 * On failure returns 401 with an empty body (no leakage).
 */
export function assertWebhookAuthorized(
  request: NextRequest
): NextResponse | null {
  const expected = (process.env.WEBHOOK_SECRET || '').trim();
  const isProd = process.env.NODE_ENV === 'production';

  if (!expected) {
    if (isProd) {
      return new NextResponse(null, { status: 401 });
    }
    return null;
  }

  const header =
    request.headers.get('x-webhook-secret') ||
    request.headers.get('X-Webhook-Secret') ||
    '';
  const auth = request.headers.get('authorization') || '';
  const bearer = auth.toLowerCase().startsWith('bearer ')
    ? auth.slice(7).trim()
    : '';
  const provided = header.trim() || bearer;

  if (provided && provided === expected) {
    return null;
  }
  return new NextResponse(null, { status: 401 });
}
