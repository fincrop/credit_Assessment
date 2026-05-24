import { NextRequest, NextResponse } from 'next/server';
import {
    blockedErrorPayload,
    fetchAgriStackUpstream,
    parseUpstreamJson,
} from '../../lib/agristackUpstream';

export async function POST(request: NextRequest) {
    try {
        const body = await request.json();

        const customHeadersStr = request.headers.get('x-custom-headers');
        let customHeaders: Record<string, string> = {};

        if (customHeadersStr) {
            try {
                customHeaders = JSON.parse(customHeadersStr);
            } catch {
                console.error('Failed to parse custom headers');
            }
        }

        const startTime = Date.now();

        const result = await fetchAgriStackUpstream('/agristack/seek', {
            method: 'POST',
            headers: {
                ...customHeaders,
            },
            body: JSON.stringify(body),
        });

        const responseTime = Date.now() - startTime;

        if (result.proxyBlocked) {
            return NextResponse.json(blockedErrorPayload(result, responseTime), {
                status: result.status || 403,
            });
        }

        const data = parseUpstreamJson(result.text);

        return NextResponse.json({
            success: result.ok,
            data,
            statusCode: result.status,
            responseTime,
            via: result.via,
        });
    } catch (error) {
        console.error('API Error:', error);
        return NextResponse.json(
            {
                success: false,
                error: {
                    code: 500,
                    message: error instanceof Error ? error.message : 'Internal server error',
                },
                statusCode: 500,
            },
            { status: 500 }
        );
    }
}
