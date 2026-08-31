'use client';

import { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAppDispatch, useAppSelector } from '../hooks/useRedux';
import Sidebar from '../components/layout/Sidebar';
import Header from '../components/layout/Header';
import ApiEndpointDisplay from '../components/sandbox/ApiEndpointDisplay';
import RequestSection from '../components/sandbox/RequestSection';
import ResponseSection from '../components/sandbox/ResponseSection';
import WebhookResponses from '../components/sandbox/WebhookResponses';
import { setToken, clearToken } from '../store/tokenSlice';
import { ENDPOINTS, getEndpointConfigs } from '../config/endpoints';
import { ingestFarmerData } from '../lib/assessmentClient';
import {
  AGRI_TOKEN_KEY,
  readSessionCreds,
  clearAgriStackSession,
} from '../lib/agristackSession';
import {
  AgriStackLoginForm,
  AgriStackLoginShell,
} from '../components/AgriStackLoginForm';

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
  const [sessionChecked, setSessionChecked] = useState(false);

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
      const raw = sessionStorage.getItem(AGRI_TOKEN_KEY);
      if (raw) {
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
      }
    } catch {
      sessionStorage.removeItem(AGRI_TOKEN_KEY);
    } finally {
      setSessionChecked(true);
    }
  }, [dispatch]);

  // Persist / clear token after session restore (avoid wiping on first mount)
  useEffect(() => {
    if (!sessionChecked) return;
    try {
      if (!accessToken) {
        // Do not clear credentials here — only drop the token mirror.
        // Full clear happens on AgriStack / AgriCredit logout.
        sessionStorage.removeItem(AGRI_TOKEN_KEY);
        return;
      }
      sessionStorage.setItem(
        AGRI_TOKEN_KEY,
        JSON.stringify({
          access_token: accessToken,
          token_type: 'Bearer',
        })
      );
    } catch {
      /* ignore quota */
    }
  }, [accessToken, sessionChecked]);

  // Restore last saved farmer IDs for continuation after Save to Platform
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('agristack_last_saved_farmers');
      if (!raw) return;
      const parsed = JSON.parse(raw) as { farmer_ids?: string[]; message?: string };
      if (Array.isArray(parsed?.farmer_ids) && parsed.farmer_ids.length > 0) {
        setSavedFarmerIds(parsed.farmer_ids);
        setIngestStatus({
          loading: false,
          message: parsed.message || `✓ Saved ${parsed.farmer_ids.length} farmer(s)`,
          success: true,
        });
      }
    } catch {
      /* ignore */
    }
  }, []);

  const handleAgriLogout = () => {
    clearAgriStackSession();
    setSavedFarmerIds([]);
    setIngestStatus({ loading: false, message: null, success: null });
    dispatch(clearToken());
  };

  useEffect(() => {
    const configs = getEndpointConfigs(accessToken);
    const config = configs[activeEndpoint];
    if (config) {
      if (prevEndpoint.current !== activeEndpoint) {
        setResponse(null);
        // Keep last-saved continuation banner; only reset in-flight save UI for this response
        setIngestStatus((prev) =>
          prev.success
            ? prev
            : { loading: false, message: null, success: null }
        );
        prevEndpoint.current = activeEndpoint;
      }
      setHeaders(JSON.stringify(config.headers, null, 2));
      const body = structuredClone(config.body) as Record<string, unknown>;
      const header = body?.header as Record<string, unknown> | undefined;
      if (header) {
        header.sender_uri = `${publicWebhookBase()}${webhookPathFor(activeEndpoint)}`;
      }
      // Autofill Token body from AgriStack gate credentials (same session)
      if (activeEndpoint === 'token') {
        const creds = readSessionCreds();
        if (creds) {
          body.username = creds.username;
          body.password = creds.password;
          body.client_id = creds.client_id || 'registry_sandbox';
          body.grant_type = 'password';
        }
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
    if (activeEndpoint !== 'token' && !accessToken) {
      setResponse({
        success: false,
        error: { code: 401, message: 'Sign in with valid AgriStack credentials first' },
        statusCode: 401,
      });
      return;
    }

    setIsLoading(true);
    setResponse(null);
    // Keep post-save continuation banner; only clear failed/in-progress save state
    setIngestStatus((prev) =>
      prev.success ? prev : { loading: false, message: null, success: null }
    );

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

  // Save successful Agristack response (or webhook body) to MongoDB farm_info
  const handleIngest = async () => {
    if (!response?.success || !response.data) return;
    setIngestStatus({ loading: true, message: null, success: null });
    try {
      // Prefer AgriStack payload; unwrap accidental Lambda/proxy wrappers
      let payload: unknown = response.data;
      if (
        payload &&
        typeof payload === 'object' &&
        !Array.isArray(payload) &&
        'data' in payload &&
        (payload as { data?: unknown }).data &&
        typeof (payload as { data?: unknown }).data === 'object'
      ) {
        const inner = (payload as { data: Record<string, unknown> }).data;
        if (inner.message || inner.header || Array.isArray(inner)) {
          payload = inner;
        }
      }
      const result = await ingestFarmerData(payload) as {
        farmer_ids: string[];
        created?: string[];
        updated?: string[];
        message?: string;
        plot_counts?: { farmer_id: string; n_plots: number; n_included: number }[];
      };
      const ids = result.farmer_ids || [];
      if (!ids.length) {
        throw new Error(result.message || 'Save succeeded but returned no farmer_ids');
      }
      setSavedFarmerIds(ids);
      const created = result.created?.length ?? 0;
      const updated = result.updated?.length ?? 0;
      let verb = 'Saved';
      if (updated > 0 && created === 0) verb = 'Updated';
      else if (updated > 0 && created > 0) verb = `Saved ${created}, updated ${updated}`;
      const plotNote =
        result.plot_counts?.length
          ? ` · ${result.plot_counts.map((p) => `${p.n_included}/${p.n_plots} plots`).join(', ')}`
          : '';
      const message = `✓ ${verb} ${ids.length} farmer(s): ${ids.join(', ')}${plotNote}`;
      setIngestStatus({
        loading: false,
        message,
        success: true,
      });
      try {
        sessionStorage.setItem(
          'agristack_last_saved_farmers',
          JSON.stringify({ farmer_ids: ids, message })
        );
      } catch {
        /* ignore */
      }
    } catch (e) {
      setIngestStatus({ loading: false, message: `✗ ${e instanceof Error ? e.message : 'Ingest failed'}`, success: false });
    }
  };

  const showIngestButton = response?.success &&
    (activeEndpoint === 'seek' || activeEndpoint === 'farmer-land-id' || activeEndpoint === 'land-parcel');

  const showContinuation =
    ingestStatus.success === true && savedFarmerIds.length > 0;

  const handleFarmersSavedFromWebhook = (payload: {
    farmer_ids: string[];
    message: string;
  }) => {
    const ids = payload.farmer_ids || [];
    setSavedFarmerIds(ids);
    setIngestStatus({
      loading: false,
      message: payload.message,
      success: ids.length > 0,
    });
  };

  if (!sessionChecked) {
    return (
      <div className="min-h-screen bg-paper flex items-center justify-center text-stone-500 text-sm">
        Loading AgriStack session…
      </div>
    );
  }

  if (!accessToken) {
    return (
      <AgriStackLoginShell>
        <AgriStackLoginForm
          title="Sign in to AgriStack"
          subtitle="Enter your AgriStack sandbox credentials. These are separate from your AgriCredit account and are only kept for this browser session."
          onSuccess={(_creds, token) => {
            if (token.access_token) {
              dispatch(
                setToken({
                  access_token: token.access_token,
                  token_type: token.token_type,
                  expires_in: token.expires_in,
                  refresh_token: token.refresh_token,
                })
              );
            }
            setActiveEndpoint('farmer-land-id');
          }}
        />
      </AgriStackLoginShell>
    );
  }

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
        <main className="flex-1 overflow-y-auto py-6">
          <div className="page-shell space-y-6">
            {showContinuation && (
              <div className="sticky top-0 z-20 bg-blue-50 border border-blue-200 rounded-xl p-5 shadow-sm">
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-blue-900">Farms saved — continue to assessment</p>
                    <p className="text-xs text-blue-800 mt-1 break-all">
                      {ingestStatus.message || `${savedFarmerIds.length} farmer(s) ready`}
                    </p>
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {savedFarmerIds.map((id) => (
                        <li key={id}>
                          <Link
                            href={`/dashboard?farmer_id=${encodeURIComponent(id)}&mode=assess`}
                            className="inline-flex text-xs font-mono px-2 py-1 rounded-md bg-white border border-blue-200 text-blue-800 hover:bg-blue-100"
                          >
                            {id}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="flex flex-wrap gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={() => {
                        const farmerId = encodeURIComponent(savedFarmerIds[0]);
                        router.push(`/dashboard?farmer_id=${farmerId}&mode=assess`);
                      }}
                      className="bg-blue-600 hover:bg-blue-500 text-white font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
                    >
                      Assess on Dashboard →
                    </button>
                    <Link
                      href="/"
                      className="inline-flex items-center bg-white border border-blue-200 hover:bg-blue-50 text-blue-900 font-semibold px-4 py-2 rounded-lg text-sm transition-colors"
                    >
                      My farmers
                    </Link>
                    <Link
                      href="/farmer/farms"
                      className="inline-flex items-center text-blue-800 hover:text-blue-950 font-medium px-3 py-2 text-sm"
                    >
                      Manage farms
                    </Link>
                  </div>
                </div>
              </div>
            )}

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

                <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-2">
                    <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span className="text-green-800 font-medium">AgriStack session active</span>
                    <span className="text-green-600 text-sm">
                      — Seek headers use this token; Token body is autofilled if you re-run it
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={handleAgriLogout}
                    className="text-sm text-stone-600 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 transition-colors"
                  >
                    Disconnect AgriStack
                  </button>
                </div>

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

                {/* Save + continue — above the large response so it stays visible */}
                {showIngestButton && (
                  <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm space-y-3">
                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-gray-800">Save to Assessment Platform</p>
                        <p className="text-xs text-stone-500 mt-0.5">
                          Write farmer land records to MongoDB, then open the dashboard to run credit assessment.
                        </p>
                        {ingestStatus.message && !showContinuation && (
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
                    </div>
                  </div>
                )}

                <ResponseSection response={response} isLoading={isLoading} />
              </>
            ) : (
              <WebhookResponses onFarmersSaved={handleFarmersSavedFromWebhook} />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
