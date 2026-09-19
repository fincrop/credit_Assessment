import { NextRequest, NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import { jobsCollection, ownedBy, requireUser } from '../../lib/jobs';

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
    if (!job) return NextResponse.json({ error: 'Job not found' }, { status: 404 });

    // 404 rather than 403 for someone else's job: a 403 confirms the id exists,
    // which is a membership oracle over every job in the system.
    if (!ownedBy(job, auth.user)) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }

    return NextResponse.json({
      job_id: id,
      stage: job.stage ?? 'queued',
      percent: typeof job.percent === 'number' ? job.percent : null,
      message: job.message ?? undefined,
      checks: Array.isArray(job.checks) ? job.checks : [],
      started_at: job.started_at ?? undefined,
      finished_at: job.finished_at ?? undefined,
      error: job.error ?? undefined,
    });
  } catch (e) {
    console.error('[classification/status]', e);
    return NextResponse.json({ error: 'Could not read job status' }, { status: 500 });
  }
}
