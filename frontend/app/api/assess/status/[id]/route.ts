import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase, isMongoTransientError } from '../../../../lib/mongodb';
import { verifyJWT } from '../../../../lib/jwt';
import { ObjectId } from 'mongodb';
import { ownerFilter } from '../../../../lib/ownerScope';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const jwtPayload = await verifyJWT(token);
    if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

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

    const ownership = ownerFilter(jwtPayload);
    const requestedByMe =
      job.requested_by === jwtPayload.email ||
      job.requested_by_user_id === jwtPayload.id;

    let farmerOwned = false;
    if (!requestedByMe && job.farmer_id) {
      const farmInfo = await db.collection('farm_info').findOne(
        { farmer_id: String(job.farmer_id), ...ownership },
        { projection: { _id: 1 } }
      );
      farmerOwned = !!farmInfo;
    }

    if (!requestedByMe && !farmerOwned) {
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
      progress: job.progress ?? null,
      result: job.result,
    });
  } catch (error) {
    const transient = isMongoTransientError(error);
    if (!transient) {
      console.error('Job Status API Error:', error);
    } else {
      console.warn('Job status: transient Mongo/DNS issue, client will retry');
    }
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : 'Internal server error',
        transient,
      },
      { status: transient ? 503 : 500 }
    );
  }
}
