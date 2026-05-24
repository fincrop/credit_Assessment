import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
    try {
        const body = await request.json();

        const startTime = Date.now();

        // Create URLSearchParams from the body
        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(body)) {
            params.append(key, String(value));
        }

        const response = await fetch('https://sandbox.agristack.gov.in/sandbox-api/nm/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                Accept: 'application/json',
            },
            body: params,
        });

        const responseTime = Date.now() - startTime;
        const responseText = await response.text();
        const contentType = response.headers.get('content-type') || '';

        let data: unknown;
        try {
            data = JSON.parse(responseText);
        } catch {
            const preview = responseText.slice(0, 300).replace(/\s+/g, ' ').trim();
            const looksLikeHtml = /^\s*<!doctype/i.test(responseText) || /^\s*<html/i.test(responseText);

            return NextResponse.json(
                {
                    success: false,
                    error: {
                        code: response.status || 502,
                        message: looksLikeHtml
                            ? 'AgriStack returned an HTML page instead of JSON. This often happens when the sandbox blocks cloud server IPs (e.g. Render/Vercel) or the service is temporarily unavailable. Try again from local dev, or contact AgriStack to allow your deployment IP.'
                            : `AgriStack returned non-JSON (${contentType || 'unknown content-type'}).`,
                        upstreamStatus: response.status,
                        upstreamContentType: contentType,
                        upstreamPreview: preview,
                    },
                    statusCode: response.status || 502,
                    responseTime,
                },
                { status: response.ok ? 502 : response.status }
            );
        }

        return NextResponse.json({
            success: response.ok,
            data: data,
            statusCode: response.status,
            responseTime,
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
