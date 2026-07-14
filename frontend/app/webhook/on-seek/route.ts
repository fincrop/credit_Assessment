import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';
import { assertWebhookAuthorized } from '../../lib/webhookAuth';

export async function POST(request: NextRequest) {
    try {
        const denied = assertWebhookAuthorized(request);
        if (denied) return denied;

        const rawBody = await request.text();
        let parsedBody: Record<string, unknown>;

        try {
            parsedBody = JSON.parse(rawBody);
        } catch {
            parsedBody = { raw: rawBody };
        }

        const { db } = await connectToDatabase();
        const collection = db.collection('webhook_responses');

        const document = {
            source: 'agristack',
            endpoint: 'on-seek',
            receivedAt: new Date(),
            headers: Object.fromEntries(request.headers.entries()),
            body: parsedBody,
            rawBody: rawBody,
        };

        const result = await collection.insertOne(document);

        console.log('✅ Webhook response saved:', result.insertedId);

        return NextResponse.json({
            success: true,
            message: 'Webhook received and saved',
            id: result.insertedId,
        });
    } catch (error) {
        console.error('❌ Webhook Error:', error);
        return NextResponse.json(
            {
                success: false,
                error: error instanceof Error ? error.message : 'Internal server error',
            },
            { status: 500 }
        );
    }
}

export async function GET() {
    return NextResponse.json({
        status: 'active',
        endpoint: '/webhook/on-seek',
        message: 'Webhook is listening for AgriStack responses',
    });
}
