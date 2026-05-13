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
            },
            body: params,
        });

        const responseTime = Date.now() - startTime;
        const data = await response.json();

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
