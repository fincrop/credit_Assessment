/**
 * Browser-side AgriStack calls. Uses the user's network IP (not the cloud server),
 * which is often allowed when Render/Vercel server IPs are blocked with 403.
 */

import { ENDPOINTS } from '../config/endpoints';

export const AGRISTACK_TOKEN_URL = 'https://sandbox.agristack.gov.in/sandbox-api/nm/token';

export type SandboxVia = 'server' | 'browser' | 'manual';

export interface SandboxResponse {
    success: boolean;
    data?: unknown;
    error?: {
        code: number;
        message: string;
        proxyBlocked?: boolean;
        corsBlocked?: boolean;
        upstreamStatus?: number;
        upstreamContentType?: string;
        upstreamPreview?: string;
        via?: SandboxVia;
    };
    statusCode: number;
    responseTime?: number;
    via?: SandboxVia;
}

export type ConnectionMode = 'auto' | 'server' | 'browser';

const MODE_KEY = 'agristack_connection_mode';

export function getConnectionMode(): ConnectionMode {
    if (typeof window === 'undefined') return 'auto';
    const v = localStorage.getItem(MODE_KEY);
    if (v === 'server' || v === 'browser' || v === 'auto') return v;
    return 'auto';
}

export function setConnectionMode(mode: ConnectionMode): void {
    localStorage.setItem(MODE_KEY, mode);
}

function isProxyBlockedPayload(data: SandboxResponse): boolean {
    return Boolean(data.error?.proxyBlocked || data.statusCode === 403);
}

function isCorsError(err: unknown): boolean {
    if (!(err instanceof TypeError)) return false;
    const msg = err.message.toLowerCase();
    return msg.includes('failed to fetch') || msg.includes('networkerror') || msg.includes('cors');
}

async function readFetchResponse(res: Response, via: SandboxVia, start: number): Promise<SandboxResponse> {
    const responseTime = Date.now() - start;
    const contentType = res.headers.get('content-type') || '';
    const text = await res.text();
    let data: unknown;
    try {
        data = JSON.parse(text);
    } catch {
        if (/^\s*<!doctype/i.test(text) || /^\s*<html/i.test(text)) {
            return {
                success: false,
                statusCode: res.status,
                responseTime,
                via,
                error: {
                    code: res.status,
                    message: `AgriStack returned HTML (${res.status}). Your network may be blocked by the sandbox WAF.`,
                    upstreamStatus: res.status,
                    upstreamContentType: contentType,
                    upstreamPreview: text.slice(0, 300).replace(/\s+/g, ' ').trim(),
                    via,
                },
            };
        }
        data = text;
    }
    return {
        success: res.ok,
        data,
        statusCode: res.status,
        responseTime,
        via,
    };
}

/** Token via Next.js proxy (server IP — often blocked on Render). */
export async function fetchTokenViaServer(body: Record<string, unknown>): Promise<SandboxResponse> {
    const start = Date.now();
    const res = await fetch('/api/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = (await res.json()) as SandboxResponse;
    return { ...data, responseTime: data.responseTime ?? Date.now() - start, via: 'server' };
}

/** Token directly from the browser (user's IP). */
export async function fetchTokenViaBrowser(body: Record<string, unknown>): Promise<SandboxResponse> {
    const start = Date.now();
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(body)) {
        params.append(key, String(value));
    }
    try {
        const res = await fetch(AGRISTACK_TOKEN_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                Accept: 'application/json',
            },
            body: params,
        });
        return readFetchResponse(res, 'browser', start);
    } catch (err) {
        return {
            success: false,
            statusCode: 0,
            responseTime: Date.now() - start,
            via: 'browser',
            error: {
                code: 0,
                message: isCorsError(err)
                    ? 'Browser blocked by CORS when calling AgriStack directly. Paste a token below (generate with curl on your machine) or run the sandbox locally.'
                    : err instanceof Error ? err.message : 'Browser request failed',
                corsBlocked: isCorsError(err),
                via: 'browser',
            },
        };
    }
}

/** Seek / land APIs via Next.js proxy. */
export async function fetchAgriStackViaServer(
    apiRoute: string,
    headers: Record<string, string>,
    body: unknown
): Promise<SandboxResponse> {
    const start = Date.now();
    const res = await fetch(apiRoute, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Custom-Headers': JSON.stringify(headers),
        },
        body: JSON.stringify(body),
    });
    const data = (await res.json()) as SandboxResponse;
    return { ...data, responseTime: data.responseTime ?? Date.now() - start, via: 'server' };
}

/** Seek / land APIs directly from browser. */
export async function fetchAgriStackViaBrowser(
    upstreamUrl: string,
    headers: Record<string, string>,
    body: unknown
): Promise<SandboxResponse> {
    const start = Date.now();
    try {
        const res = await fetch(upstreamUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
        });
        return readFetchResponse(res, 'browser', start);
    } catch (err) {
        return {
            success: false,
            statusCode: 0,
            responseTime: Date.now() - start,
            via: 'browser',
            error: {
                code: 0,
                message: isCorsError(err)
                    ? 'Browser blocked by CORS. Switch to server mode, use a local dev server, or ask AgriStack to allow your deployment origin.'
                    : err instanceof Error ? err.message : 'Browser request failed',
                corsBlocked: isCorsError(err),
                via: 'browser',
            },
        };
    }
}

export async function fetchTokenSmart(body: Record<string, unknown>): Promise<SandboxResponse> {
    const mode = getConnectionMode();
    if (mode === 'browser') return fetchTokenViaBrowser(body);
    if (mode === 'server') return fetchTokenViaServer(body);

    const server = await fetchTokenViaServer(body);
    if (server.success) return server;
    if (isProxyBlockedPayload(server)) {
        const browser = await fetchTokenViaBrowser(body);
        if (browser.success) {
            return {
                ...browser,
                data: {
                    ...(typeof browser.data === 'object' && browser.data !== null ? browser.data : {}),
                    _fallback: 'server_blocked_used_browser',
                },
            };
        }
        return {
            ...browser,
            error: {
                ...(browser.error || { code: 403, message: 'Token request failed' }),
                message:
                    'Server proxy blocked (403) and browser-direct also failed. ' +
                    'Generate a token on your machine with the curl command below and paste it in the connection panel.',
                proxyBlocked: true,
            },
        };
    }
    return server;
}

export async function fetchAgriStackSmart(
    endpointId: string,
    apiRoute: string,
    upstreamUrl: string,
    headers: Record<string, string>,
    body: unknown
): Promise<SandboxResponse> {
    const mode = getConnectionMode();
    if (mode === 'browser') return fetchAgriStackViaBrowser(upstreamUrl, headers, body);
    if (mode === 'server') return fetchAgriStackViaServer(apiRoute, headers, body);

    const server = await fetchAgriStackViaServer(apiRoute, headers, body);
    if (server.success) return server;

    const blocked =
        isProxyBlockedPayload(server) ||
        server.statusCode === 403 ||
        (typeof server.data === 'string' && /403 Forbidden/i.test(server.data));

    if (blocked) {
        return fetchAgriStackViaBrowser(upstreamUrl, headers, body);
    }
    return server;
}

export function endpointUpstreamUrl(endpointId: string): string {
    return ENDPOINTS.find((e) => e.id === endpointId)?.url || '';
}

export function tokenCurlExample(body: Record<string, unknown>): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(body)) {
        params.append(key, String(value));
    }
    return `curl -X POST "${AGRISTACK_TOKEN_URL}" \\
  -H "Content-Type: application/x-www-form-urlencoded" \\
  -d "${params.toString()}"`;
}
