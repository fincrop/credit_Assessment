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
    const job = await (await jobsCollection()).findOne({ _id: new ObjectId(id) });
    if (!job || !ownedBy(job, auth.user)) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }
    return NextResponse.json({
      job_id: id,
      stage: job.stage ?? 'queued',
      percent: typeof job.percent === 'number' ? job.percent : null,
      message: job.message ?? undefined,
      error: job.error ?? undefined,
    });
  } catch (e) {
    console.error('[monitoring/status]', e);
    return NextResponse.json({ error: 'Could not read job status' }, { status: 500 });
  }
}
