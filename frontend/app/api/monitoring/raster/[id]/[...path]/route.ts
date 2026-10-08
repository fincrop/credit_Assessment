import { NextRequest, NextResponse } from 'next/server';
import { ObjectId } from 'mongodb';
import { jobsCollection, ownedBy, pipelineBaseUrl, pipelineHeaders, requireUser } from '../../../lib/jobs';

/**
 * Raster products of a raster-engine monitoring job: the PNG overlays the map
 * draws, the COGs for GIS users, and products.json. Same auth and ownership
 * check as the download route; the service key never reaches the browser.
 */
function allowedPath(path: string): boolean {
  if (!path || path.includes('..') || path.includes('\\') || path.startsWith('/')) return false;
  return path === 'products.json' || path.startsWith('png/') || path.startsWith('cog/');
}

export async function GET(
  req: NextRequest,
  context: { params: Promise<{ id: string; path: string[] }> }
) {
  try {
    const auth = await requireUser(req);
    if ('response' in auth) return auth.response;
    const { id, path: segments } = await context.params;
    if (!id || !ObjectId.isValid(id)) {
      return NextResponse.json({ error: 'Valid job id is required' }, { status: 400 });
    }
    const path = (segments || []).join('/');
    if (!allowedPath(path)) {
      return NextResponse.json({ error: 'Invalid raster path' }, { status: 400 });
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
      return NextResponse.json({ error: 'No monitoring service configured to serve rasters.' }, { status: 503 });
    }
    const upstream = await fetch(
      `${base}/v1/jobs/monitor/${id}/raster/${path.split('/').map(encodeURIComponent).join('/')}`,
      {
        headers: pipelineHeaders(),
        signal: AbortSignal.timeout(Number(process.env.PIPELINE_DOWNLOAD_TIMEOUT_MS || 120000)),
      }
    );
    if (!upstream.ok || !upstream.body) {
      const detail = (await upstream.text().catch(() => '')).slice(0, 200);
      return NextResponse.json(
        { error: `Service could not serve ${path}. HTTP ${upstream.status}. ${detail}`.trim() },
        { status: upstream.status === 404 ? 404 : 502 }
      );
    }
    const name = path.split('/').pop() || 'raster';
    const headers: Record<string, string> = {
      'Content-Type': upstream.headers.get('Content-Type') || 'application/octet-stream',
      'Cache-Control': 'private, max-age=3600',
    };
    const length = upstream.headers.get('Content-Length');
    if (length) headers['Content-Length'] = length;
    if (path.startsWith('cog/')) headers['Content-Disposition'] = `attachment; filename="${name}"`;
    return new NextResponse(upstream.body, { status: 200, headers });
  } catch (e) {
    console.error('[monitoring/raster]', e);
    return NextResponse.json({ error: 'Raster request failed' }, { status: 500 });
  }
}
