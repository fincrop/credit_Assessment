import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
    try {
        const body = await request.json();

        // Get custom headers from the X-Custom-Headers header
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

        const response = await fetch('https://sandbox.agristack.gov.in/sandbox-api/agristack/seek', {
            method: 'POST',
            headers: {
                ...customHeaders,
            },
            body: JSON.stringify(body),
        });

        const responseTime = Date.now() - startTime;
        
        const responseText = await response.text();
        let data;
        try {
            data = JSON.parse(responseText);
        } catch {
            data = responseText;
        }

        return NextResponse.json({
            success: response.ok,
            data: data,
            statusCode: response.status,
            responseTime,
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
