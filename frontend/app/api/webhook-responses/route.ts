import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';

export async function GET(request: NextRequest) {
    try {
        const { searchParams } = new URL(request.url);
        const type = searchParams.get('type') || 'all';
        const page = parseInt(searchParams.get('page') || '1') || 1;
        const limit = parseInt(searchParams.get('limit') || '50') || 50;
        const skip = (page - 1) * limit;

        const { db } = await connectToDatabase();
        
        let responses: any[] = [];
        let totalCount = 0;

        if (type === 'all') {
            const legacyCollection = db.collection('webhook_responses');
            const farmersCollection = db.collection('webhook_farmers_responses');
            const kdssCollection = db.collection('webhook_kdss_responses');

            const [legacyCount, farmersCount, kdssCount] = await Promise.all([
                 legacyCollection.countDocuments(),
                 farmersCollection.countDocuments(),
                 kdssCollection.countDocuments(),
            ]);
            totalCount = legacyCount + farmersCount + kdssCount;

            const targetLimit = skip + limit;
            const [legacy, farmers, kdss] = await Promise.all([
                legacyCollection.find({}).sort({ receivedAt: -1 }).limit(targetLimit).toArray(),
                farmersCollection.find({}).sort({ receivedAt: -1 }).limit(targetLimit).toArray(),
                kdssCollection.find({}).sort({ receivedAt: -1 }).limit(targetLimit).toArray(),
            ]);

            responses = [...legacy, ...farmers, ...kdss]
                .sort((a, b) => new Date(b.receivedAt).getTime() - new Date(a.receivedAt).getTime())
                .slice(skip, skip + limit);
        } else if (type === 'farmers') {
            totalCount = await db.collection('webhook_farmers_responses').countDocuments();
            responses = await db.collection('webhook_farmers_responses').find({}).sort({ receivedAt: -1 }).skip(skip).limit(limit).toArray();
        } else if (type === 'kdss') {
            totalCount = await db.collection('webhook_kdss_responses').countDocuments();
            responses = await db.collection('webhook_kdss_responses').find({}).sort({ receivedAt: -1 }).skip(skip).limit(limit).toArray();
        } else if (type === 'legacy') {
            totalCount = await db.collection('webhook_responses').countDocuments();
            responses = await db.collection('webhook_responses').find({}).sort({ receivedAt: -1 }).skip(skip).limit(limit).toArray();
        }

        return NextResponse.json({
            success: true,
            totalCount,
            page,
            limit,
            totalPages: Math.ceil(totalCount / limit),
            count: responses.length,
            data: responses,
        });
    } catch (error) {
        console.error('❌ Error fetching webhook responses:', error);
        return NextResponse.json(
            {
                success: false,
                error: error instanceof Error ? error.message : 'Failed to fetch webhook responses',
            },
            { status: 500 }
        );
    }
}
