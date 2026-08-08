import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { ownerFilter } from '../../../lib/ownerScope';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

/**
 * GET /api/assessments/latest?farmer_id= — latest successful credit_assessments for deep-link fallback.
 */
export async function GET(req: NextRequest) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const farmerId = (req.nextUrl.searchParams.get('farmer_id') || '').trim();
  if (!farmerId) {
    return NextResponse.json({ error: 'farmer_id required' }, { status: 400 });
  }

  try {
    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);

    const owned = await db.collection('farm_info').findOne(
      { farmer_id: farmerId, ...ownerFilter(jwtPayload) },
      { projection: { _id: 1 } }
    );
    if (!owned) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 });
    }

    // Prefer SUCCESS so a failed re-run does not hide the last good score.
    let doc = await db
      .collection('credit_assessments')
      .find({ farmer_id: farmerId, status: 'SUCCESS' })
      .sort({ assessment_date: -1, updated_at: -1, created_at: -1 })
      .limit(1)
      .next();

    if (!doc) {
      doc = await db
        .collection('credit_assessments')
        .find({
          farmer_id: farmerId,
          status: { $nin: ['FAILED', 'failed'] },
          'farmer_level.index_score': { $exists: true, $ne: null },
        })
        .sort({ assessment_date: -1, updated_at: -1, created_at: -1 })
        .limit(1)
        .next();
    }

    if (doc) {
      const { _id, ...rest } = doc;
      return NextResponse.json({
        success: true,
        source: 'credit_assessments',
        assessment: { ...rest, _id: _id?.toString?.() ?? String(_id) },
      });
    }

    // Fallback: last successful job result (same payload shape the dashboard uses live)
    const job = await db.collection('jobs').findOne(
      {
        farmer_id: farmerId,
        status: 'SUCCESS',
        result: { $exists: true, $ne: null },
      },
      { sort: { completed_at: -1, created_at: -1 } }
    );

    if (job?.result && typeof job.result === 'object') {
      return NextResponse.json({
        success: true,
        source: 'job',
        job_id: job._id?.toString?.() ?? undefined,
        assessment: job.result,
      });
    }

    return NextResponse.json({ error: 'No assessment history' }, { status: 404 });
  } catch (error) {
    console.error('GET /api/assessments/latest error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
