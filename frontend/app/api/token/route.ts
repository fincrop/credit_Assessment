import { NextRequest, NextResponse } from 'next/server';
import {
    agristackProxyEnabled,
    callAgristackLambda,
} from '../../lib/agristackLambdaProxy';

function bodyString(value: unknown): string {
    return typeof value === 'string' ? value.trim() : '';
}

export async function POST(request: NextRequest) {
    try {
        const body = (await request.json()) as Record<string, unknown>;
        const startTime = Date.now();

        const username = bodyString(body.username);
        const password = bodyString(body.password);
        const clientId =
            bodyString(body.client_id) || 'registry_sandbox';
        const grantType = bodyString(body.grant_type) || 'password';

        if (!username || !password) {
            return NextResponse.json(
                {
                    success: false,
                    error: {
                        code: 400,
                        message:
                            'Provide valid AgriStack credentials (username and password are required in the request body)',
                    },
                    statusCode: 400,
                },
                { status: 400 }
            );
        }

        // Mumbai Lambda — avoids non-India cloud IP blocks on Token
        if (agristackProxyEnabled()) {
            const { json, responseTime, status } = await callAgristackLambda(
                'token',
                {
                    username,
                    password,
                    client_id: clientId,
                    grant_type: grantType,
                }
            );

            const data = json.data as Record<string, unknown> | undefined;
            const accessToken =
                (typeof data?.access_token === 'string' && data.access_token) ||
                (typeof json.access_token === 'string' && json.access_token) ||
                null;

            if (status === 401 || (!accessToken && (json.error || json.success === false))) {
                const errObj = json.error as
                    | { message?: string; code?: number }
                    | string
                    | undefined;
                const message =
                    typeof errObj === 'string'
                        ? errObj
                        : errObj?.message ||
                          (typeof data?.error === 'string'
                              ? data.error
                              : 'AgriStack token proxy failed');
                return NextResponse.json(
                    {
                        success: false,
                        error: { code: status || 401, message },
                        data,
                        statusCode: status || 401,
                        responseTime,
                        via: 'lambda-ap-south-1',
                    },
                    { status: status === 401 ? 401 : 200 }
                );
            }

            return NextResponse.json({
                success: Boolean(accessToken),
                data: data || json,
                statusCode: Number(json.statusCode ?? json.status ?? status),
                responseTime: Number(json.responseTime ?? json.ms ?? responseTime),
                via: 'lambda-ap-south-1',
            });
        }

        const params = new URLSearchParams();
        params.append('grant_type', grantType);
        params.append('client_id', clientId);
        params.append('username', username);
        params.append('password', password);

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

        if (!response.ok) {
            const upstream = data as { error_description?: string; error?: string; message?: string };
            const detail =
                upstream?.error_description ||
                upstream?.message ||
                (typeof upstream?.error === 'string' ? upstream.error : null) ||
                'Provide valid AgriStack credentials';
            return NextResponse.json(
                {
                    success: false,
                    error: {
                        code: response.status,
                        message: detail,
                    },
                    data,
                    statusCode: response.status,
                    responseTime,
                },
                { status: response.status === 401 || response.status === 403 ? response.status : 401 }
            );
        }

        return NextResponse.json({
            success: true,
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
