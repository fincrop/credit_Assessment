import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../../lib/mongodb';
import { ObjectId } from 'mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  try {
    const { id: jobId } = await context.params;

    if (!jobId || !ObjectId.isValid(jobId)) {
      return NextResponse.json({ error: 'Valid job_id is required' }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const jobsCollection = db.collection('jobs');

    const job = await jobsCollection.findOne({ _id: new ObjectId(jobId) });

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }

    return NextResponse.json({
      job_id: job._id.toString(),
      farmer_id: job.farmer_id,
      status: job.status,
      created_at: job.created_at,
      started_at: job.started_at,
      completed_at: job.completed_at,
      error: job.error,
      result: job.result
    });

  } catch (error) {
    console.error('Job Status API Error:', error);
    return NextResponse.json({ 
      error: error instanceof Error ? error.message : 'Internal server error' 
    }, { status: 500 });
  }
}
