'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useAppDispatch, useAppSelector } from '../hooks/useRedux';
import Sidebar from '../components/layout/Sidebar';
import Header from '../components/layout/Header';
import ApiEndpointDisplay from '../components/sandbox/ApiEndpointDisplay';
import RequestSection from '../components/sandbox/RequestSection';
import ResponseSection from '../components/sandbox/ResponseSection';
import WebhookResponses from '../components/sandbox/WebhookResponses';
import { setToken } from '../store/tokenSlice';
import { ENDPOINTS, getEndpointConfigs } from '../config/endpoints';
import { ingestFarmerData } from '../lib/assessmentClient';

export default function AgristackPage() {
  const router = useRouter();
  const dispatch = useAppDispatch();
  const accessToken = useAppSelector((state) => state.token.accessToken);

  const [activeTab, setActiveTab] = useState<'sandbox' | 'webhook'>('sandbox');
  const [activeEndpoint, setActiveEndpoint] = useState('token');
  const [headers, setHeaders] = useState('');
  const [requestBody, setRequestBody] = useState('');
  const [bulkFarmerIds, setBulkFarmerIds] = useState('');
  const [response, setResponse] = useState<{
    success: boolean;
    data?: unknown;
    error?: { code: number; message: string };
    statusCode: number;
    responseTime?: number;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [ingestStatus, setIngestStatus] = useState<{ loading: boolean; message: string | null; success: boolean | null }>({
    loading: false, message: null, success: null,
  });
  const [savedFarmerIds, setSavedFarmerIds] = useState<string[]>([]);

  const isTokenUpdate = useRef(false);
  const prevEndpoint = useRef(activeEndpoint);
  const tokenRestored = useRef(false);

  const currentEndpoint = ENDPOINTS.find(e => e.id === activeEndpoint) || ENDPOINTS[0];
  const endpointConfigs = getEndpointConfigs(accessToken);
  const currentConfig = endpointConfigs[activeEndpoint] || endpointConfigs.seek;

  /** Public base URL AgriStack can POST webhooks to (never localhost). */
  const publicWebhookBase = () => {
    const configured = String(process.env.NEXT_PUBLIC_APP_DOMAIN || '')
      .replace(/\/$/, '')
      .trim();
    if (configured && !/localhost|127\.0\.0\.1/i.test(configured)) {
      return configured;
    }
    if (typeof window !== 'undefined') {
      const origin = `${window.location.protocol}//${window.location.host}`;
      if (!/localhost|127\.0\.0\.1/i.test(origin)) return origin;
    }
    return configured || 'http://localhost:3000';
  };

  const webhookPathFor = (endpoint: string) => {
    if (endpoint === 'krishi-dss-seek') return '/webhook/kdss/on-seek';
    if (endpoint === 'farmer-land-id' || endpoint === 'seek') return '/webhook/farmers/on-seek';
    return '/webhook/on-seek';
  };

  // Restore AgriStack access token from sessionStorage (survives reload, clears on tab close)
  useEffect(() => {
    if (tokenRestored.current) return;
    tokenRestored.current = true;
    try {
      const raw = sessionStorage.getItem('agristack_access_token');
      if (!raw) return;
      const parsed = JSON.parse(raw) as {
        access_token?: string;
        token_type?: string;
        expires_in?: number;
        refresh_token?: string;
      };
      if (parsed?.access_token) {
        dispatch(setToken({
          access_token: parsed.access_token,
          token_type: parsed.token_type,
          expires_in: parsed.expires_in,
          refresh_token: parsed.refresh_token,
        }));
      }
    } catch {
      sessionStorage.removeItem('agristack_access_token');
    }
  }, [dispatch]);

  // Persist token whenever Redux token changes
  useEffect(() => {
    if (!accessToken) return;
    try {
      sessionStorage.setItem(
        'agristack_access_token',
        JSON.stringify({
          access_token: accessToken,
          token_type: 'Bearer',
        })
      );
    } catch {
      /* ignore quota */
    }
  }, [accessToken]);

  useEffect(() => {
    const configs = getEndpointConfigs(accessToken);
    const config = configs[activeEndpoint];
    if (config) {
      if (prevEndpoint.current !== activeEndpoint) {
        setResponse(null);
        setIngestStatus({ loading: false, message: null, success: null });
        setSavedFarmerIds([]);
        prevEndpoint.current = activeEndpoint;
      }
      setHeaders(JSON.stringify(config.headers, null, 2));
      // Prefer NEXT_PUBLIC_APP_DOMAIN (Cloudflare tunnel) so AgriStack can reach us
      const body = structuredClone(config.body) as Record<string, unknown>;
      const header = body?.header as Record<string, unknown> | undefined;
      if (header) {
        header.sender_uri = `${publicWebhookBase()}${webhookPathFor(activeEndpoint)}`;
      }
      setRequestBody(JSON.stringify(body, null, 2));
    }
  }, [activeEndpoint, accessToken]);

  /**
   * Call AgriStack via Next.js API routes (server-side proxy).
   * Browser → /api/token|/api/agristack|/api/krishi-dss-seek → AgriStack.
   * Avoids CORS: sandbox.agristack.gov.in does not allow browser origins
   * like http://localhost:3000, so direct fetch from the page always fails.
   */
  const callAgriStackViaProxy = async (
    endpoint: string,
    body: Record<string, unknown>,
    customHeaders: Record<string, string>
  ) => {
    const startTime = Date.now();
    const config = endpointConfigs[endpoint] || currentConfig;

    // sender_uri must be a public URL (Cloudflare tunnel), not localhost
    if (body?.header && typeof body.header === 'object') {
      const header = body.header as Record<string, unknown>;
      header.sender_uri = `${publicWebhookBase()}${webhookPathFor(endpoint)}`;
    }

    try {
      const proxyHeaders: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (endpoint !== 'token') {
        proxyHeaders['X-Custom-Headers'] = JSON.stringify(customHeaders);
      }

      const res = await fetch(config.apiRoute, {
        method: 'POST',
        headers: proxyHeaders,
        body: JSON.stringify(body),
      });

      const payload = await res.json();
      // Proxy routes already return { success, data, error, statusCode, responseTime }
      if (payload && typeof payload === 'object' && 'statusCode' in payload) {
        return payload;
      }
      return {
        success: res.ok,
        data: payload,
        statusCode: res.status,
        responseTime: Date.now() - startTime,
      };
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        error: {
          code: 0,
          message: raw || 'Failed to reach AgriStack proxy',
        },
        statusCode: 0,
        responseTime: Date.now() - startTime,
      };
    }
  };

  const handleRun = async () => {
    setIsLoading(true);
    setResponse(null);
    setIngestStatus({ loading: false, message: null, success: null });
    setSavedFarmerIds([]);

    try {
      const parsedHeaders = JSON.parse(headers);
      // Deduplicate bulk IDs while preserving order
      const rawIds = bulkFarmerIds.split(',').map(id => id.trim()).filter(id => id);
      const ids = [...new Set(rawIds)];

      if ((activeEndpoint === 'seek' || activeEndpoint === 'farmer-land-id') && ids.length > 0) {
        const results = [];
        for (const id of ids) {
          let parsedBody = JSON.parse(requestBody);
          if (parsedBody.header) {
            parsedBody.header.message_id = crypto.randomUUID();
            parsedBody.header.receiver_id = crypto.randomUUID();
          }
          if (parsedBody.message) {
            parsedBody.message.transaction_id = crypto.randomUUID();
            if (Array.isArray(parsedBody.message.search_request)) {
              parsedBody.message.search_request.forEach((req: Record<string, unknown>) => {
                req.reference_id = crypto.randomUUID();
              });
            }
          }
          if (activeEndpoint === 'seek') {
            if (parsedBody.message?.search_request?.[0]?.search_criteria?.query?.query_params?.[0]?.farmer_identifier) {
              parsedBody.message.search_request[0].search_criteria.query.query_params[0].farmer_identifier.farmer_id = id;
            }
          } else if (activeEndpoint === 'farmer-land-id') {
            if (parsedBody.message?.search_request?.[0]?.search_criteria?.query?.query_params?.[0]) {
              parsedBody.message.search_request[0].search_criteria.query.query_params[0].farmer_id = id;
            }
          }
          try {
            const result = await callAgriStackViaProxy(activeEndpoint, parsedBody, parsedHeaders);
            results.push({ farmer_id: id, status: result.statusCode, response: result.data, ...result });
          } catch (err) {
            results.push({ farmer_id: id, status: 500, error: err instanceof Error ? err.message : 'Unknown error' });
          }
        }
        setResponse({ success: true, statusCode: 200, data: results } as never);
      } else {
        const parsedBody = JSON.parse(requestBody);
        if (activeEndpoint !== 'token' && parsedBody) {
          if (parsedBody.header) {
            parsedBody.header.message_id = crypto.randomUUID();
            parsedBody.header.receiver_id = crypto.randomUUID();
          }
          if (parsedBody.message) {
            parsedBody.message.transaction_id = crypto.randomUUID();
            if (Array.isArray(parsedBody.message.search_request)) {
              parsedBody.message.search_request.forEach((req: Record<string, unknown>) => {
                req.reference_id = crypto.randomUUID();
              });
            }
          }
          setRequestBody(JSON.stringify(parsedBody, null, 2));
        }
        
        const result = await callAgriStackViaProxy(activeEndpoint, parsedBody, parsedHeaders);
        setResponse(result);

        const tokenData = result?.data as
          | { access_token?: string; token_type?: string; expires_in?: number; refresh_token?: string }
          | undefined;
        if (activeEndpoint === 'token' && result.success && tokenData?.access_token) {
          isTokenUpdate.current = true;
          dispatch(
            setToken({
              access_token: tokenData.access_token,
              token_type: tokenData.token_type,
              expires_in: tokenData.expires_in,
              refresh_token: tokenData.refresh_token,
            })
          );
        }
      }
    } catch (error) {
      setResponse({ success: false, error: { code: 400, message: error instanceof Error ? error.message : 'Invalid JSON' }, statusCode: 400 });
    } finally {
      setIsLoading(false);
    }
  };

  // Save successful Agristack response to MongoDB farm_info
  const handleIngest = async () => {
    if (!response?.success || !response.data) return;
    setIngestStatus({ loading: true, message: null, success: null });
    try {
      const result = await ingestFarmerData(response.data) as {
        farmer_ids: string[];
        created?: string[];
        updated?: string[];
        message?: string;
      };
      setSavedFarmerIds(result.farmer_ids || []);
      const created = result.created?.length ?? 0;
      const updated = result.updated?.length ?? 0;
      let verb = 'Saved';
      if (updated > 0 && created === 0) verb = 'Updated';
      else if (updated > 0 && created > 0) verb = `Saved ${created}, updated ${updated}`;
      setIngestStatus({
        loading: false,
        message: `✓ ${verb} ${result.farmer_ids.length} farmer(s): ${result.farmer_ids.join(', ')}`,
        success: true,
      });
    } catch (e) {
      setIngestStatus({ loading: false, message: `✗ ${e instanceof Error ? e.message : 'Ingest failed'}`, success: false });
    }
  };

  const showIngestButton = response?.success &&
    (activeEndpoint === 'seek' || activeEndpoint === 'farmer-land-id' || activeEndpoint === 'land-parcel');

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar
        endpoints={ENDPOINTS}
        activeEndpoint={activeEndpoint}
        onSelectEndpoint={(id: string) => {
          setActiveTab('sandbox');
          setActiveEndpoint(id);
        }}
      />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header activeTab={activeTab} onTabChange={setActiveTab} />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-5xl mx-auto space-y-6">
            {activeTab === 'sandbox' ? (
              <>
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3">
                  <div className="flex items-start gap-3">
                    <svg className="w-5 h-5 text-emerald-600 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <div>
                      <p className="text-sm font-medium text-emerald-800">Server-side AgriStack proxy</p>
                      <p className="text-xs text-emerald-700 mt-1">
                        Calls go through Next.js routes (<code className="font-mono">/api/token</code>,{' '}
                        <code className="font-mono">/api/agristack</code>) so the browser never talks to
                        sandbox.agristack.gov.in directly — that avoids CORS on localhost.
                      </p>
                    </div>
                  </div>
                </div>

                {accessToken && (
                  <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 flex items-center gap-2">
                    <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span className="text-green-800 font-medium">Token Active</span>
                    <span className="text-green-600 text-sm">- Token is auto-filled in Seek API headers</span>
                  </div>
                )}

                <ApiEndpointDisplay method={currentEndpoint.method} url={currentEndpoint.url} />

                {(activeEndpoint === 'seek' || activeEndpoint === 'farmer-land-id') && (
                  <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm">
                    <label htmlFor="bulkIds" className="block text-sm font-semibold text-gray-800 mb-2">
                      Comma-separated Farmer IDs (Batch Execution)
                    </label>
                    <textarea
                      id="bulkIds"
                      value={bulkFarmerIds}
                      onChange={(e) => setBulkFarmerIds(e.target.value)}
                      placeholder="e.g. 10001921019, 57272407248"
                      className="w-full text-sm font-mono text-gray-900 border border-gray-300 rounded-md p-3 placeholder-gray-400 focus:ring-2 focus:ring-green-500 focus:border-green-500 outline-none block"
                      rows={2}
                    />
                    <p className="mt-2 text-xs text-stone-500 font-medium">
                      If provided, the sandbox will sequentially execute the payload for each farmer ID.
                    </p>
                  </div>
                )}

                <RequestSection
                  headers={headers}
                  onHeadersChange={setHeaders}
                  requestBody={requestBody}
                  onRequestBodyChange={setRequestBody}
                  onRun={handleRun}
                  isLoading={isLoading}
                />

                <ResponseSection response={response} isLoading={isLoading} />

                {/* Save to Platform button */}
                {showIngestButton && (
                  <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-semibold text-gray-800">Save to Assessment Platform</p>
                      <p className="text-xs text-stone-500 mt-0.5">
                        Extract farmer land records from this response and write them to <code className="font-mono">farm_info</code> in MongoDB.
                      </p>
                      {ingestStatus.message && (
                        <p className={`text-xs mt-1.5 font-medium ${ingestStatus.success ? 'text-green-600' : 'text-red-600'}`}>
                          {ingestStatus.message}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={handleIngest}
                      disabled={ingestStatus.loading}
                      className="flex-shrink-0 bg-green-600 hover:bg-green-500 disabled:bg-gray-300 text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
                    >
                      {ingestStatus.loading ? 'Saving…' : '⬆ Save to Platform'}
                    </button>
                    {ingestStatus.success && savedFarmerIds.length > 0 && (
                      <button
                        onClick={() => {
                          const farmerId = encodeURIComponent(savedFarmerIds[0]);
                          router.push(`/dashboard?farmer_id=${farmerId}`);
                        }}
                        className="flex-shrink-0 bg-blue-600 hover:bg-blue-500 text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
                      >
                        Go to Dashboard →
                      </button>
                    )}
                  </div>
                )}
              </>
            ) : (
              <WebhookResponses />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
