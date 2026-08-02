import { MongoClient, ServerApiVersion } from 'mongodb';
import { randomUUID } from 'crypto';

/** Strip accidental markdown links pasted into env (e.g. [https://x](https://x)). */
function cleanBaseUrl(raw) {
  const s = String(raw || '').trim();
  if (!s) return '';
  const md = s.match(/\((https?:\/\/[^)\s]+)\)/);
  if (md) return md[1].replace(/\/+$/, '');
  const plain = s.match(/https?:\/\/[^\s\]]+/);
  return (plain ? plain[0] : s).replace(/\/+$/, '');
}

const LAMBDA_BASE =
  cleanBaseUrl(process.env.LAMBDA_PUBLIC_BASE_URL) ||
  'https://e2luibgn3cnl42vb4dp6ddcxe40jmbpc.lambda-url.ap-south-1.on.aws';

const DB_NAME =
  process.env.MONGODB_DATABASE ||
  process.env.MONGODB_DB ||
  'agristack';

/** Upstream AgriStack fetch timeout (ms) */
const UPSTREAM_TIMEOUT_MS = Number(process.env.AGRISTACK_FETCH_TIMEOUT_MS || 10000);

/** Reuse Mongo client across warm invocations */
let mongoClient = null;

async function getDb() {
  const uri = (process.env.MONGODB_URI || '').trim();
  if (!uri) {
    throw new Error('MONGODB_URI is not set on the Lambda');
  }
  if (!mongoClient) {
    const client = new MongoClient(uri, {
      serverApi: {
        version: ServerApiVersion.v1,
        strict: true,
        deprecationErrors: true,
      },
      maxPoolSize: 1,
    });
    try {
      await client.connect();
      mongoClient = client;
    } catch (err) {
      // Do not leave a truthy-but-unconnected client (would wedge warm container)
      mongoClient = null;
      try {
        await client.close();
      } catch {
        /* ignore */
      }
      throw err;
    }
  }
  return mongoClient.db(DB_NAME);
}

function json(statusCode, body) {
  return {
    statusCode,
    headers: {
      'Content-Type': 'application/json',
    },
    body: typeof body === 'string' ? body : JSON.stringify(body, null, 2),
  };
}

function methodOf(event) {
  return event.requestContext?.http?.method || event.httpMethod || 'GET';
}

function pathOf(event) {
  const raw =
    event.rawPath ||
    event.requestContext?.http?.path ||
    event.path ||
    '/';
  return String(raw).replace(/\/+$/, '') || '/';
}

function parseBody(event) {
  if (!event?.body) return { parsed: {}, rawText: '' };
  const raw = event.isBase64Encoded
    ? Buffer.from(event.body, 'base64').toString('utf8')
    : event.body;
  if (typeof raw === 'object' && raw !== null) {
    return { parsed: raw, rawText: JSON.stringify(raw) };
  }
  try {
    return { parsed: JSON.parse(raw || '{}'), rawText: String(raw || '') };
  } catch {
    return { parsed: { raw: String(raw) }, rawText: String(raw || '') };
  }
}

function headerMap(event) {
  const h = event.headers || {};
  const out = {};
  for (const [k, v] of Object.entries(h)) {
    out[String(k).toLowerCase()] = v;
  }
  return out;
}

/**
 * Shared-secret gate.
 * - PROXY_SECRET / AGRISTACK_PROXY_SECRET: required for token/seek actions when set
 * - WEBHOOK_SECRET: optional for inbound AgriStack callbacks (only if they can send it)
 * When unset → allow (sandbox). Never put IAM on webhook path.
 */
function assertSharedSecret(event, envKeys, { requiredInStrict = false } = {}) {
  let expected = '';
  for (const key of envKeys) {
    const v = (process.env[key] || '').trim();
    if (v) {
      expected = v;
      break;
    }
  }
  if (!expected) {
    if (requiredInStrict && (process.env.REQUIRE_PROXY_SECRET || '').trim() === '1') {
      return json(401, { success: false, error: 'Proxy secret not configured' });
    }
    return null;
  }
  const headers = headerMap(event);
  const provided =
    (headers['x-agristack-proxy-secret'] || '').trim() ||
    (headers['x-webhook-secret'] || '').trim() ||
    ((headers.authorization || '').toLowerCase().startsWith('bearer ')
      ? headers.authorization.slice(7).trim()
      : '');
  if (provided && provided === expected) return null;
  return json(401, { success: false, error: 'Unauthorized' });
}

function webhookTarget(path) {
  if (path.endsWith('/webhook/farmers/on-seek')) {
    return {
      collection: 'webhook_farmers_responses',
      source: 'agristack-farmers',
      endpoint: 'on-seek',
    };
  }
  if (path.endsWith('/webhook/kdss/on-seek')) {
    return {
      collection: 'webhook_kdss_responses',
      source: 'agristack-kdss',
      endpoint: 'on-seek',
    };
  }
  if (path.endsWith('/webhook/on-seek')) {
    return {
      collection: 'webhook_responses',
      source: 'agristack',
      endpoint: 'on-seek',
    };
  }
  return null;
}

async function saveWebhook(event, target) {
  // Webhook path: only WEBHOOK_SECRET (optional). Do not require PROXY_SECRET —
  // AgriStack cannot send our proxy header.
  const denied = assertSharedSecret(event, ['WEBHOOK_SECRET']);
  if (denied) return denied;

  const { parsed, rawText } = parseBody(event);
  const db = await getDb();
  const doc = {
    source: target.source,
    endpoint: target.endpoint,
    receivedAt: new Date(),
    headers: headerMap(event),
    body: parsed,
    rawBody: rawText,
    via: 'lambda-ap-south-1',
  };
  const result = await db.collection(target.collection).insertOne(doc);
  return json(200, {
    success: true,
    message: 'Webhook received and saved',
    id: String(result.insertedId),
    collection: target.collection,
  });
}

async function fetchWithTimeout(url, options = {}, timeoutMs = UPSTREAM_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (e) {
    if (e?.name === 'AbortError') {
      const err = new Error(`Upstream timeout after ${timeoutMs}ms`);
      err.code = 'UPSTREAM_TIMEOUT';
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function callToken({ username, password, client_id }) {
  const params = new URLSearchParams({
    grant_type: 'password',
    client_id: client_id || 'registry_sandbox',
    username,
    password,
  });
  const started = Date.now();
  const res = await fetchWithTimeout(
    'https://sandbox.agristack.gov.in/sandbox-api/nm/token',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Accept: 'application/json',
      },
      body: params,
    }
  );
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  return {
    ok: res.ok,
    status: res.status,
    contentType: res.headers.get('content-type') || '',
    looksLikeHtml: /^\s*<!doctype/i.test(text) || /^\s*<html/i.test(text),
    ms: Date.now() - started,
    data,
  };
}

/**
 * Build a farmer Seek body. Prefer forwarding a full seek_body from the UI;
 * this builder is only for probe / partial params.
 */
function buildFarmerSeekBody(opts) {
  const {
    farmerId,
    senderUri,
    state_lgd_code = '9',
    year = '2022-2023',
    season = 'Rabi',
    aadhaar_type = 'E',
    mapper_id = 'i1004:o1007',
    query_name = 'agristack_croparea_v3_get_data',
  } = opts;
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
              mapper_id,
              query_name,
              query_params: [
                {
                  farmer_identifier: { farmer_id: String(farmerId) },
                  aadhaar_type,
                  state_lgd_code: String(state_lgd_code),
                  year: String(year),
                  season: String(season),
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

function ensureSenderUri(seekBody, fallbackUri) {
  if (!seekBody || typeof seekBody !== 'object') return seekBody;
  const header = seekBody.header;
  if (!header || typeof header !== 'object') return seekBody;
  const current = String(header.sender_uri || '').trim();
  if (!current || /localhost|127\.0\.0\.1/i.test(current)) {
    return {
      ...seekBody,
      header: { ...header, sender_uri: fallbackUri },
    };
  }
  return seekBody;
}

async function postJsonUpstream(url, { accessToken, body, extraHeaders = {} }) {
  const started = Date.now();
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text.slice(0, 800);
  }
  return {
    ok: res.ok,
    status: res.status,
    contentType: res.headers.get('content-type') || '',
    looksLikeHtml: /^\s*<!doctype/i.test(text) || /^\s*<html/i.test(text),
    ms: Date.now() - started,
    data,
    request_body: body,
  };
}

async function recordOutboundSeek({ kind, seekBody, upstream }) {
  try {
    const db = await getDb();
    const correlationId =
      upstream?.data?.message?.correlation_id ||
      upstream?.data?.correlation_id ||
      null;
    const transactionId = seekBody?.message?.transaction_id || null;
    await db.collection('agristack_outbound_seeks').insertOne({
      kind,
      createdAt: new Date(),
      via: 'lambda-ap-south-1',
      transaction_id: transactionId,
      correlation_id: correlationId,
      sender_uri: seekBody?.header?.sender_uri || null,
      ack_status: upstream?.data?.message?.ack_status || null,
      upstream_status: upstream?.status ?? null,
    });
  } catch (e) {
    console.warn('outbound_seek_record_failed', e?.message || e);
  }
}

async function callFarmerSeek(input) {
  const farmersWebhook = `${LAMBDA_BASE}/webhook/farmers/on-seek`;
  const {
    accessToken,
    farmerId,
    senderUri = farmersWebhook,
    seek_body,
    state_lgd_code,
    year,
    season,
    aadhaar_type,
    mapper_id,
    query_name,
  } = input;

  let body;
  if (seek_body && typeof seek_body === 'object') {
    body = ensureSenderUri(structuredClone(seek_body), senderUri || farmersWebhook);
  } else {
    if (!farmerId) {
      throw Object.assign(new Error('farmer_id or seek_body required'), {
        statusCode: 400,
      });
    }
    body = buildFarmerSeekBody({
      farmerId,
      senderUri: senderUri || farmersWebhook,
      state_lgd_code,
      year,
      season,
      aadhaar_type,
      mapper_id,
      query_name,
    });
  }

  const upstream = await postJsonUpstream(
    'https://sandbox.agristack.gov.in/sandbox-api/agristack/seek',
    { accessToken, body }
  );
  await recordOutboundSeek({ kind: 'farmer_seek', seekBody: body, upstream });
  return {
    ...upstream,
    request_farmer_id: farmerId || null,
    sender_uri: body?.header?.sender_uri || null,
  };
}

async function callKdssSeek(input) {
  const kdssWebhook = `${LAMBDA_BASE}/webhook/kdss/on-seek`;
  const { accessToken, seek_body, senderUri = kdssWebhook, entity_id, extraHeaders } =
    input;

  if (!seek_body || typeof seek_body !== 'object') {
    throw Object.assign(
      new Error('seek_body required for action=kdss_seek (forward UI KDSS JSON)'),
      { statusCode: 400 }
    );
  }

  const body = ensureSenderUri(structuredClone(seek_body), senderUri || kdssWebhook);
  const headers = { ...(extraHeaders || {}) };
  if (entity_id) headers['entity-id'] = entity_id;

  const upstream = await postJsonUpstream(
    'https://sandbox.agristack.gov.in/sandbox-api/krishi-dss-seek',
    { accessToken, body, extraHeaders: headers }
  );
  await recordOutboundSeek({ kind: 'kdss_seek', seekBody: body, upstream });
  return {
    ...upstream,
    sender_uri: body?.header?.sender_uri || null,
  };
}

async function handleAction(event) {
  // Gate proxy actions (not webhooks) when PROXY_SECRET is set
  const denied = assertSharedSecret(event, [
    'AGRISTACK_PROXY_SECRET',
    'PROXY_SECRET',
  ]);
  if (denied) return denied;

  const { parsed: body } = parseBody(event);
  const action = String(body.action || 'token').toLowerCase();
  const farmersWebhook = `${LAMBDA_BASE}/webhook/farmers/on-seek`;
  const kdssWebhook = `${LAMBDA_BASE}/webhook/kdss/on-seek`;

  try {
    if (action === 'token') {
      const result = await callToken(body);
      // Return real tokens to the Next.js BFF (server-to-server). Gate with PROXY_SECRET.
      return json(200, {
        test: 'token',
        geo_likely_ok: !result.looksLikeHtml,
        success: result.ok,
        statusCode: result.status,
        responseTime: result.ms,
        ok: result.ok,
        status: result.status,
        contentType: result.contentType,
        looksLikeHtml: result.looksLikeHtml,
        ms: result.ms,
        data: result.data,
        access_token: result.data?.access_token || null,
      });
    }

    if (action === 'seek' || action === 'farmer_seek') {
      const accessToken = body.access_token || body.token;
      if (!accessToken) {
        return json(400, { error: 'access_token required for action=seek' });
      }
      const result = await callFarmerSeek({
        accessToken,
        farmerId: body.farmer_id,
        senderUri: body.sender_uri || farmersWebhook,
        seek_body: body.seek_body || body.agristack_body || null,
        state_lgd_code: body.state_lgd_code,
        year: body.year,
        season: body.season,
        aadhaar_type: body.aadhaar_type,
        mapper_id: body.mapper_id,
        query_name: body.query_name,
      });
      return json(200, {
        test: 'seek',
        geo_likely_ok: !result.looksLikeHtml,
        success: result.ok,
        statusCode: result.status,
        responseTime: result.ms,
        data: result.data,
        ...result,
        // avoid echoing full request body with PII in every response
        request_body: undefined,
      });
    }

    if (action === 'kdss_seek' || action === 'krishi_dss_seek') {
      const accessToken = body.access_token || body.token;
      if (!accessToken) {
        return json(400, { error: 'access_token required for action=kdss_seek' });
      }
      const result = await callKdssSeek({
        accessToken,
        seek_body: body.seek_body || body.agristack_body || null,
        senderUri: body.sender_uri || kdssWebhook,
        entity_id: body.entity_id,
        extraHeaders: body.extra_headers || {},
      });
      return json(200, {
        test: 'kdss_seek',
        geo_likely_ok: !result.looksLikeHtml,
        success: result.ok,
        statusCode: result.status,
        responseTime: result.ms,
        data: result.data,
        ...result,
        request_body: undefined,
      });
    }

    if (action === 'token_and_seek') {
      const tokenResult = await callToken(body);
      if (!tokenResult.ok || !tokenResult.data?.access_token) {
        return json(200, {
          test: 'token_and_seek',
          step: 'token_failed',
          geo_likely_ok: !tokenResult.looksLikeHtml,
          token: {
            status: tokenResult.status,
            ms: tokenResult.ms,
            ok: tokenResult.ok,
            data: tokenResult.data,
          },
        });
      }
      const seekResult = await callFarmerSeek({
        accessToken: tokenResult.data.access_token,
        farmerId: body.farmer_id,
        senderUri: body.sender_uri || farmersWebhook,
        seek_body: body.seek_body || body.agristack_body || null,
        state_lgd_code: body.state_lgd_code,
        year: body.year,
        season: body.season,
        aadhaar_type: body.aadhaar_type,
        mapper_id: body.mapper_id,
        query_name: body.query_name,
      });
      return json(200, {
        test: 'token_and_seek',
        geo_likely_ok: !tokenResult.looksLikeHtml && !seekResult.looksLikeHtml,
        token: {
          status: tokenResult.status,
          ms: tokenResult.ms,
          ok: tokenResult.ok,
        },
        seek: { ...seekResult, request_body: undefined },
      });
    }

    return json(400, {
      error:
        'Unknown action. Use token | seek | farmer_seek | kdss_seek | token_and_seek',
    });
  } catch (e) {
    if (e?.code === 'UPSTREAM_TIMEOUT') {
      return json(504, {
        success: false,
        error: e.message,
        code: 'UPSTREAM_TIMEOUT',
      });
    }
    const status = e?.statusCode || 500;
    return json(status, {
      success: false,
      error: e instanceof Error ? e.message : String(e),
    });
  }
}

export const handler = async (event) => {
  const method = methodOf(event);
  const path = pathOf(event);

  if (method === 'OPTIONS') {
    return json(204, {});
  }

  try {
    const target = webhookTarget(path);
    if (target) {
      if (method === 'GET') {
        return json(200, {
          status: 'active',
          endpoint: path,
          message: `Webhook listening → Mongo ${target.collection}`,
        });
      }
      if (method === 'POST') {
        return await saveWebhook(event, target);
      }
      return json(405, { error: 'Method not allowed' });
    }

    if (method === 'GET') {
      return json(200, {
        ok: true,
        message:
          'AgriStack Lambda — POST action=token|seek|kdss_seek|token_and_seek, or POST /webhook/*/on-seek',
        webhooks: {
          farmers: `${LAMBDA_BASE}/webhook/farmers/on-seek`,
          legacy: `${LAMBDA_BASE}/webhook/on-seek`,
          kdss: `${LAMBDA_BASE}/webhook/kdss/on-seek`,
        },
        notes: [
          'Prefer seek_body (full UI JSON) over farmer_id-only builder',
          'Set AGRISTACK_PROXY_SECRET to gate token/seek actions',
          'Do not put IAM auth on webhook paths',
        ],
      });
    }

    if (method === 'POST') {
      return await handleAction(event);
    }

    return json(405, { error: 'Method not allowed' });
  } catch (e) {
    console.error('handler_error', e);
    return json(500, {
      success: false,
      error: e instanceof Error ? e.message : String(e),
    });
  }
};
