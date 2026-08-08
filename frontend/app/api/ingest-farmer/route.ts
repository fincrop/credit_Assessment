import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../lib/mongodb';
import { verifyJWT } from '../../lib/jwt';
import { ownerFields } from '../../lib/ownerScope';
import {
  resolveFarmersForIngest,
  upsertFarmersFromDocs,
} from '../../lib/ingestAgriStack';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';

export async function POST(req: NextRequest) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const jwtPayload = await verifyJWT(token);
    if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const ownership = ownerFields(jwtPayload);

    const { agristack_response } = await req.json();
    if (!agristack_response) {
      return NextResponse.json({ error: 'Missing agristack_response in body' }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);

    const { farmers, correlationIds } = await resolveFarmersForIngest(
      db,
      agristack_response,
      ownership
    );

    if (farmers.length === 0) {
      const lambdaHint = process.env.AGRISTACK_PROXY_URL
        ? 'Confirm NEXT_PUBLIC_APP_DOMAIN points at the Mumbai Lambda webhook base, wait ~30–60s, open Webhook Responses, use Save to Platform there, then retry.'
        : 'Confirm sender_uri / NEXT_PUBLIC_APP_DOMAIN is reachable, wait ~30–60s, refresh Webhook Responses, then retry Save (or Save from the Webhook tab).';
      return NextResponse.json(
        {
          error: correlationIds.length
            ? `Seek ACK only so far. No matching webhook farmer payload for correlation_id=${correlationIds.join(', ')}. ${lambdaHint}`
            : 'No farmer records found yet. Seek returned ACK only; wait for webhook callback, then Save from Webhook Responses or retry Save to Platform.',
        },
        { status: 400 }
      );
    }

    const result = await upsertFarmersFromDocs(db, farmers, ownership, jwtPayload);
    if (result.farmer_ids.length === 0) {
      return NextResponse.json(
        {
          success: false,
          error:
            result.errors[0]?.error ||
            'Failed to save farmers to MongoDB. Check server logs for details.',
          farmer_ids: [],
          errors: result.errors,
          plot_counts: result.plot_counts,
        },
        { status: 500 }
      );
    }

    const parts: string[] = [];
    if (result.created.length) parts.push(`created ${result.created.length}`);
    if (result.updated.length) parts.push(`updated ${result.updated.length}`);
    const totalPlots = result.plot_counts.reduce((s, p) => s + p.n_plots, 0);

    return NextResponse.json({
      success: true,
      message:
        `Successfully ingested ${result.farmer_ids.length} farmer(s)` +
        (parts.length ? ` (${parts.join(', ')})` : '') +
        (totalPlots ? ` · ${totalPlots} plot(s) stored` : ''),
      farmer_ids: result.farmer_ids,
      created: result.created,
      updated: result.updated,
      plot_counts: result.plot_counts,
      errors: result.errors.length > 0 ? result.errors : undefined,
    });
  } catch (error) {
    console.error('Ingest API Error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
