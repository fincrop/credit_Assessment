import { NextRequest, NextResponse } from 'next/server';

function pickCredential(fromBody: unknown, ...envKeys: string[]): string {
    const bodyVal = typeof fromBody === 'string' ? fromBody.trim() : '';
    if (bodyVal) return bodyVal;
    for (const key of envKeys) {
        const v = (process.env[key] || '').trim();
        if (v) return v;
    }
    return '';
}

export async function POST(request: NextRequest) {
    try {
        const body = (await request.json()) as Record<string, unknown>;
        const startTime = Date.now();

        const merged: Record<string, unknown> = {
            ...body,
            grant_type: pickCredential(body.grant_type) || 'password',
            client_id:
                pickCredential(
                    body.client_id,
                    'AGRISTACK_CLIENT_ID',
                    'NEXT_PUBLIC_AGRISTACK_CLIENT_ID'
                ) || 'registry_sandbox',
            username: pickCredential(
                body.username,
                'AGRISTACK_USERNAME',
                'NEXT_PUBLIC_AGRISTACK_USERNAME'
            ),
            password: pickCredential(
                body.password,
                'AGRISTACK_PASSWORD',
                'NEXT_PUBLIC_AGRISTACK_PASSWORD'
            ),
        };

        if (!merged.username) {
            return NextResponse.json(
                {
                    success: false,
                    error: {
                        code: 400,
                        message:
                            'username is required — set AGRISTACK_USERNAME in frontend/.env.local and restart Next.js',
                    },
                    statusCode: 400,
                },
                { status: 400 }
            );
        }

        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(merged)) {
            if (value != null && value !== '') params.append(key, String(value));
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
            const looksLikeHtml =
                /^\s*<!doctype/i.test(responseText) || /^\s*<html/i.test(responseText);

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
