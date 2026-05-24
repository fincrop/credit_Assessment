/**
 * Server-side AgriStack fetch (Next.js API routes).
 * Cloud hosts (Render/Vercel) are often blocked with HTTP 403 HTML from AgriStack WAF.
 */

export const AGRISTACK_API_BASE = 'https://sandbox.agristack.gov.in/sandbox-api';

export type AgriStackUpstreamResult = {
    ok: boolean;
    status: number;
    contentType: string;
    text: string;
    proxyBlocked: boolean;
    via: 'direct' | 'proxy';
};

function proxyBase(): string | null {
    const raw = process.env.AGRISTACK_PROXY_BASE?.trim();
    return raw ? raw.replace(/\/$/, '') : null;
}

function looksLikeBlockedHtml(text: string, status: number): boolean {
    if (status !== 403 && status !== 502 && status !== 503) return false;
    return /^\s*<!doctype/i.test(text) || /^\s*<html/i.test(text) || /\b403 Forbidden\b/i.test(text);
}

async function fetchOnce(url: string, init: RequestInit): Promise<Response> {
    return fetch(url, {
        ...init,
        headers: {
            Accept: 'application/json, text/plain, */*',
            'User-Agent': 'AgriStack-Sandbox-Client/1.0',
            ...(init.headers as Record<string, string> | undefined),
        },
    });
}

/** Call AgriStack from the server; optionally retry via AGRISTACK_PROXY_BASE on 403. */
export async function fetchAgriStackUpstream(
    path: string,
    init: RequestInit
): Promise<AgriStackUpstreamResult> {
    const directUrl = `${AGRISTACK_API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
    const direct = await fetchOnce(directUrl, init);
    const directText = await direct.text();
    const directType = direct.headers.get('content-type') || '';

    if (!looksLikeBlockedHtml(directText, direct.status)) {
        return {
            ok: direct.ok,
            status: direct.status,
            contentType: directType,
            text: directText,
            proxyBlocked: false,
            via: 'direct',
        };
    }

    const proxy = proxyBase();
    if (!proxy) {
        return {
            ok: false,
            status: direct.status,
            contentType: directType,
            text: directText,
            proxyBlocked: true,
            via: 'direct',
        };
    }

    const proxyUrl = `${proxy}${path.startsWith('/') ? path : `/${path}`}`;
    const proxied = await fetchOnce(proxyUrl, init);
    const proxiedText = await proxied.text();
    const proxiedType = proxied.headers.get('content-type') || '';

    return {
        ok: proxied.ok,
        status: proxied.status,
        contentType: proxiedType,
        text: proxiedText,
        proxyBlocked: looksLikeBlockedHtml(proxiedText, proxied.status),
        via: 'proxy',
    };
}

export function parseUpstreamJson(text: string): unknown {
    try {
        return JSON.parse(text);
    } catch {
        return text;
    }
}

export function blockedErrorPayload(result: AgriStackUpstreamResult, responseTime: number) {
    const preview = result.text.slice(0, 300).replace(/\s+/g, ' ').trim();
    return {
        success: false as const,
        error: {
            code: result.status || 403,
            message:
                'AgriStack blocked this request from the deployment server (HTTP 403). ' +
                'The sandbox often rejects cloud/datacenter IPs. Use browser-direct mode (uses your network IP) or paste a token generated locally.',
            proxyBlocked: true,
            upstreamStatus: result.status,
            upstreamContentType: result.contentType,
            upstreamPreview: preview,
            via: result.via,
        },
        statusCode: result.status || 403,
        responseTime,
    };
}
