import { NextRequest, NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { ownerFields, ownerFilter } from '../../../lib/ownerScope';
import { assignPlotKeysClient } from '../../../lib/plotKey';
import {
  agristackProxyEnabled,
  agristackLambdaWebhookUrls,
  callAgristackLambda,
} from '../../../lib/agristackLambdaProxy';
import {
  extractCorrelationIds,
  findWebhookBodiesByCorrelation,
  extractFarmersFromPayload,
  upsertFarmersFromDocs,
  parseAgriStackPayloads,
} from '../../../lib/ingestAgriStack';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const WEBHOOK_WAIT_MS = Number(process.env.AGRISTACK_WEBHOOK_WAIT_MS || 90000);
const WEBHOOK_POLL_MS = 2500;

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function buildSeekBody(farmerId: string, senderUri: string, opts: {
  state_lgd_code?: string;
  year?: string;
  season?: string;
  aadhaar_type?: string;
}) {
  const now = new Date().toISOString();
  return {
    header: {
      version: '0.1.0',
      sender_id: 'f20f0d3c-ccdb-4d96-ba7a-cf8476d7c15a',
      message_id: randomUUID(),
      message_ts: now,
      sender_uri: senderUri,
      receiver_id: randomUUID(),
      total_count: 1,
      is_msg_encrypted: false,
    },
    message: {
      search_request: [
        {
          locale: 'en',
          timestamp: now,
          reference_id: randomUUID(),
          search_criteria: {
            query: {
              mapper_id: 'i1004:o1007',
              query_name: 'agristack_croparea_v3_get_data',
              query_params: [
                {
                  farmer_identifier: { farmer_id: String(farmerId) },
                  aadhaar_type: opts.aadhaar_type || 'E',
                  state_lgd_code: String(opts.state_lgd_code || '9'),
                  year: String(opts.year || '2022-2023'),
                  season: String(opts.season || 'Rabi'),
                },
              ],
            },
            consent: {},
            reg_type: 'agristack_farmer',
            pagination: { page_size: 1000, page_number: 1 },
            query_type: 'namedQuery',
          },
        },
      ],
      transaction_id: randomUUID(),
    },
    signature: 'Signature string',
  };
}

function farmInfoResponse(doc: Record<string, unknown>) {
  const farms = assignPlotKeysClient(
    Array.isArray(doc.farms) ? (doc.farms as Record<string, unknown>[]) : []
  );
  return {
    farmer_id: doc.farmer_id,
    name: doc.name || doc.farmer_name || null,
    mobile: doc.mobile || null,
    state: doc.state || null,
    district: doc.district || null,
    village: doc.village || null,
    state_lgd_code: doc.state_lgd_code || null,
    district_lgd_code: doc.district_lgd_code || null,
    farmer_benefits: doc.farmer_benefits || null,
    source: doc.source || null,
    farms,
    updated_at: doc.updated_at || null,
  };
}

/**
 * POST /api/agristack/prepare-farmer
 * Body: { farmer_id, username, password, client_id?, force?, state_lgd_code?, year?, season? }
 * Uses the caller's AgriStack credentials (never server env defaults) + Mumbai Lambda.
 */
export async function POST(req: NextRequest) {
  try {
    const token = req.cookies.get('auth-token')?.value;
    if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const jwtPayload = await verifyJWT(token);
    if (!jwtPayload?.email) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    const ownership = ownerFields(jwtPayload);

    const body = await req.json();
    const farmerId = String(body.farmer_id || '').trim();
    const force = Boolean(body.force);
    if (!farmerId) {
      return NextResponse.json({ error: 'farmer_id required' }, { status: 400 });
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);

    if (!force) {
      const existing = await db.collection('farm_info').findOne({
        farmer_id: farmerId,
        ...ownerFilter(jwtPayload),
      });
      const farms = Array.isArray(existing?.farms) ? existing!.farms : [];
      if (existing && farms.length > 0) {
        return NextResponse.json({
          success: true,
          skipped_seek: true,
          stage: 'cached',
          farmer_id: farmerId,
          farm_info: farmInfoResponse(existing as Record<string, unknown>),
          farms: assignPlotKeysClient(farms as Record<string, unknown>[]),
        });
      }
    }

    if (!agristackProxyEnabled()) {
      return NextResponse.json(
        {
          error:
            'AGRISTACK_PROXY_URL is not set. Configure Mumbai Lambda proxy for auto-prepare.',
        },
        { status: 503 }
      );
    }

    const username = String(body.username || '').trim();
    const password = String(body.password || '').trim();
    const clientId =
      String(body.client_id || '').trim() || 'registry_sandbox';
    if (!username || !password) {
      return NextResponse.json(
        {
          error:
            'AgriStack credentials required. Sign in with your AgriStack username and password first.',
          stage: 'credentials',
        },
        { status: 401 }
      );
    }

    const hooks = agristackLambdaWebhookUrls();
    const senderUri = hooks?.farmers;
    if (!senderUri) {
      return NextResponse.json({ error: 'Lambda webhook URL unavailable' }, { status: 503 });
    }

    // 1) Token
    const tokenRes = await callAgristackLambda('token', {
      username,
      password,
      client_id: clientId,
      grant_type: 'password',
    });
    const tokenData = tokenRes.json.data as Record<string, unknown> | undefined;
    const accessToken =
      (typeof tokenData?.access_token === 'string' && tokenData.access_token) ||
      (typeof tokenRes.json.access_token === 'string' && tokenRes.json.access_token) ||
      null;
    if (!accessToken) {
      return NextResponse.json(
        {
          error: 'AgriStack token failed',
          detail: tokenRes.json,
          stage: 'token',
        },
        { status: 502 }
      );
    }

    // 2) Seek
    const seekBody = buildSeekBody(farmerId, senderUri, {
      state_lgd_code: body.state_lgd_code,
      year: body.year,
      season: body.season,
      aadhaar_type: body.aadhaar_type,
    });
    const seekRes = await callAgristackLambda('seek', {
      access_token: accessToken,
      seek_body: seekBody,
      sender_uri: senderUri,
    });
    const seekData = (seekRes.json.data ?? seekRes.json) as Record<string, unknown>;
    const ackPayloads = parseAgriStackPayloads(seekData);
    let correlationIds = extractCorrelationIds(ackPayloads);
    // Also dig into nested Lambda response shapes
    if (!correlationIds.length) {
      const msg = (seekData as { message?: { correlation_id?: string } }).message;
      if (msg?.correlation_id) correlationIds = [msg.correlation_id];
    }

    if (!seekRes.ok && !correlationIds.length) {
      return NextResponse.json(
        {
          error: 'AgriStack Seek failed',
          detail: seekRes.json,
          stage: 'seek',
        },
        { status: 502 }
      );
    }

    // 3) Poll webhook until farmer payload arrives
    const deadline = Date.now() + WEBHOOK_WAIT_MS;
    let farmers: Record<string, unknown>[] = [];
    let stage = 'waiting_webhook';

    while (Date.now() < deadline) {
      if (correlationIds.length) {
        const bodies = await findWebhookBodiesByCorrelation(db, correlationIds);
        for (const b of bodies) {
          farmers = farmers.concat(extractFarmersFromPayload(b, ownership));
        }
      }
      // Prefer the requested farmer_id if multiple
      farmers = farmers.filter((f) => String(f.farmer_id) === farmerId);
      if (farmers.length > 0) break;
      await sleep(WEBHOOK_POLL_MS);
    }

    if (farmers.length === 0) {
      return NextResponse.json(
        {
          error: correlationIds.length
            ? `Timed out waiting for AgriStack webhook (correlation_id=${correlationIds.join(', ')}). Retry Prepare farms, or use /agristack Webhook Responses → Save.`
            : 'Seek did not return a correlation_id and no webhook arrived. Check Lambda sender_uri / AgriStack sandbox.',
          stage,
          correlation_ids: correlationIds,
        },
        { status: 504 }
      );
    }

    stage = 'ingest';
    const upsert = await upsertFarmersFromDocs(db, farmers, ownership, jwtPayload);
    if (!upsert.farmer_ids.includes(farmerId) && upsert.farmer_ids.length === 0) {
      return NextResponse.json(
        {
          error: upsert.errors[0]?.error || 'Failed to save farmer after webhook',
          stage,
          errors: upsert.errors,
        },
        { status: 500 }
      );
    }

    const saved = await db.collection('farm_info').findOne({
      farmer_id: farmerId,
      ...ownerFilter(jwtPayload),
    });
    if (!saved) {
      return NextResponse.json(
        { error: 'Ingest completed but farm_info not found for this user', stage },
        { status: 500 }
      );
    }

    const farms = assignPlotKeysClient(
      Array.isArray(saved.farms) ? (saved.farms as Record<string, unknown>[]) : []
    );

    return NextResponse.json({
      success: true,
      skipped_seek: false,
      stage: 'ready',
      farmer_id: farmerId,
      correlation_ids: correlationIds,
      farm_info: farmInfoResponse(saved as Record<string, unknown>),
      farms,
      plot_counts: upsert.plot_counts,
    });
  } catch (error) {
    console.error('prepare-farmer error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
