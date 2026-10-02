import { NextRequest, NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import { jobsCollection, ownedBy, pipelineBaseUrl, pipelineHeaders, requireUser } from '../../lib/jobs';

const SERVER_FORMATS = new Set(['shapefile', 'geotiff', 'png']);

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
    const format = (req.nextUrl.searchParams.get('format') || '').toLowerCase();
    if (!SERVER_FORMATS.has(format)) {
      return NextResponse.json({ error: `Unsupported format "${format}".` }, { status: 400 });
    }
    const job = await (await jobsCollection()).findOne({ _id: new ObjectId(id) });
    if (!job || !ownedBy(job, auth.user)) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }
    if (job.stage !== 'complete') {
      return NextResponse.json({ error: 'Job is not complete' }, { status: 409 });
    }
    const base = pipelineBaseUrl();
    if (!base) {
      return NextResponse.json({ error: 'No monitoring service configured to render this format.' }, { status: 503 });
    }
    const upstream = await fetch(
      `${base}/v1/jobs/monitor/${id}/download?format=${encodeURIComponent(format)}`,
      {
        headers: pipelineHeaders(),
        signal: AbortSignal.timeout(Number(process.env.PIPELINE_DOWNLOAD_TIMEOUT_MS || 120000)),
      }
    );
    if (!upstream.ok || !upstream.body) {
      const detail = (await upstream.text().catch(() => '')).slice(0, 200);
      return NextResponse.json(
        { error: `Service could not produce ${format}. HTTP ${upstream.status}. ${detail}`.trim() },
        { status: 502 }
      );
    }
    const ext = format === 'shapefile' ? 'zip' : format === 'geotiff' ? 'tif' : 'png';
    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        'Content-Type': upstream.headers.get('Content-Type') || 'application/octet-stream',
        'Content-Disposition':
          upstream.headers.get('Content-Disposition') || `attachment; filename="${id}.${ext}"`,
        'Cache-Control': 'private, no-store',
      },
    });
  } catch (e) {
    console.error('[monitoring/download]', e);
    return NextResponse.json({ error: 'Download failed' }, { status: 500 });
  }
}
