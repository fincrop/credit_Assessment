import { NextRequest, NextResponse } from 'next/server';

/**
 * Optional shared-secret gate for AgriStack webhook POSTs.
 * When WEBHOOK_SECRET is unset, requests are allowed (local/dev).
 * When set, require header `x-webhook-secret: <secret>` (or Bearer token).
 */
export function assertWebhookAuthorized(
  request: NextRequest
): NextResponse | null {
  const expected = (process.env.WEBHOOK_SECRET || '').trim();
  if (!expected) {
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
  return NextResponse.json({ error: 'Unauthorized webhook' }, { status: 401 });
}
