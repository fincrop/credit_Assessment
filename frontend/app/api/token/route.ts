import { NextRequest, NextResponse } from 'next/server';
import {
    blockedErrorPayload,
    fetchAgriStackUpstream,
    parseUpstreamJson,
} from '../../lib/agristackUpstream';

export async function POST(request: NextRequest) {
    try {
        const body = await request.json();
        const startTime = Date.now();

        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(body)) {
            params.append(key, String(value));
        }

        const result = await fetchAgriStackUpstream('/nm/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: params,
        });

        const responseTime = Date.now() - startTime;

        if (result.proxyBlocked) {
            return NextResponse.json(blockedErrorPayload(result, responseTime), {
                status: result.status || 403,
            });
        }

        const data = parseUpstreamJson(result.text);
        if (typeof data === 'string' && (data.startsWith('<!') || data.startsWith('<'))) {
            return NextResponse.json(blockedErrorPayload(result, responseTime), {
                status: result.status || 502,
            });
        }

        return NextResponse.json({
            success: result.ok,
            data,
            statusCode: result.status,
            responseTime,
            via: result.via,
        });
    } catch (error) {
        console.error('Token API Error:', error);
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
