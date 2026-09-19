import { NextRequest, NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import { jobsCollection, ownedBy, requireUser, withTypedRegionName } from '../../lib/jobs';

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;

    const { id } = await context.params;
    if (!id || !ObjectId.isValid(id)) {
      return NextResponse.json({ error: 'Valid job id is required' }, { status: 400 });
    }

    const collection = await jobsCollection();
    const job = await collection.findOne({ _id: new ObjectId(id) });
    if (!job || !ownedBy(job, auth.user)) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }
    if (job.stage !== 'complete') {
      return NextResponse.json(
        { error: `Job is ${job.stage ?? 'queued'}, not complete` },
        { status: 409 }
      );
    }
    if (!job.result) {
      return NextResponse.json(
        { error: 'Job completed without writing a result' },
        { status: 500 }
      );
    }

    return NextResponse.json({
      ...withTypedRegionName(job.result as Record<string, unknown>, job.inputs),
      job_id: id,
    });
  } catch (e) {
    console.error('[classification/result]', e);
    return NextResponse.json({ error: 'Could not read the result' }, { status: 500 });
  }
}
