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

  const currentEndpoint = ENDPOINTS.find(e => e.id === activeEndpoint) || ENDPOINTS[0];
  const endpointConfigs = getEndpointConfigs(accessToken);
  const currentConfig = endpointConfigs[activeEndpoint] || endpointConfigs.seek;

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
      setRequestBody(JSON.stringify(config.body, null, 2));
    }
  }, [activeEndpoint, accessToken]);

  const handleRun = async () => {
    setIsLoading(true);
    setResponse(null);
    setIngestStatus({ loading: false, message: null, success: null });
    setSavedFarmerIds([]);

    try {
      const parsedHeaders = JSON.parse(headers);
      const ids = bulkFarmerIds.split(',').map(id => id.trim()).filter(id => id);

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
            const res = await fetch(currentConfig.apiRoute, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-Custom-Headers': JSON.stringify(parsedHeaders) },
              body: JSON.stringify(parsedBody),
            });
            const data = await res.json();
            results.push({ farmer_id: id, status: res.status, response: data });
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
        const res = await fetch(currentConfig.apiRoute, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Custom-Headers': JSON.stringify(parsedHeaders) },
          body: JSON.stringify(parsedBody),
        });
        const data = await res.json();
        setResponse(data);
        if (activeEndpoint === 'token' && data.success && data.data?.access_token) {
          isTokenUpdate.current = true;
          dispatch(setToken({
            access_token: data.data.access_token,
            token_type: data.data.token_type,
            expires_in: data.data.expires_in,
            refresh_token: data.data.refresh_token,
          }));
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
      const result = await ingestFarmerData(response.data);
      setSavedFarmerIds(result.farmer_ids || []);
      setIngestStatus({
        loading: false,
        message: `✓ Saved ${result.farmer_ids.length} farmer(s): ${result.farmer_ids.join(', ')}`,
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
                    <p className="mt-2 text-xs text-gray-500 font-medium">
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
                      <p className="text-xs text-gray-500 mt-0.5">
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
