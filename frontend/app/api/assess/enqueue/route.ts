import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { farmer_id, pm_kisan_enrolled, has_crop_insurance } = body;

    if (!farmer_id) {
      return NextResponse.json({ error: 'farmer_id is required' }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const jobsCollection = db.collection('jobs');

    const jobDoc = {
      farmer_id,
      pm_kisan_enrolled: !!pm_kisan_enrolled,
      has_crop_insurance: !!has_crop_insurance,
      status: 'QUEUED',
      created_at: new Date(),
      updated_at: new Date(),
      result: null,
      error: null
    };

    const result = await jobsCollection.insertOne(jobDoc);

    return NextResponse.json({
      success: true,
      job_id: result.insertedId.toString(),
      status: 'QUEUED'
    });

  } catch (error) {
    console.error('Enqueue API Error:', error);
    return NextResponse.json({ 
      error: error instanceof Error ? error.message : 'Internal server error' 
    }, { status: 500 });
  }
}
