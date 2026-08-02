'use client';

import { useState, useEffect } from 'react';

interface WebhookResponse {
    _id: string;
    source: string;
    endpoint: string;
    receivedAt: string;
    body: Record<string, unknown>;
    rawBody: string;
}

// Syntax highlighting function for JSON
function highlightJson(json: string): string {
    const escaped = json
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    return escaped
        .replace(/"([^"\\]*(\\.[^"\\]*)*)"/g, (match, content) => {
            return `"<span style="color: #ce9178">${content}</span>"`;
        })
        .replace(/\b(-?\d+\.?\d*)\b/g, '<span style="color: #b5cea8">$1</span>')
        .replace(/\b(true|false)\b/g, '<span style="color: #569cd6">$1</span>')
        .replace(/\bnull\b/g, '<span style="color: #569cd6">null</span>')
        .replace(/"<span style="color: #ce9178">([^<]+)<\/span>"\s*:/g,
            '"<span style="color: #9cdcfe">$1</span>":')
        .replace(/([{}\[\]])/g, '<span style="color: #ffd700">$1</span>')
        .replace(/(<\/span>"):/g, '$1<span style="color: #d4d4d4">:</span>')
        .replace(/,/g, '<span style="color: #d4d4d4">,</span>');
}

export default function WebhookResponses() {
    const [responses, setResponses] = useState<WebhookResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [selectedType, setSelectedType] = useState<string>('all');
    const [viewMode, setViewMode] = useState<'table' | 'json'>('table');
    const [page, setPage] = useState(1);
    const [totalPages, setTotalPages] = useState(1);
    const [totalCount, setTotalCount] = useState(0);

    // Helper to safely extract AgriStack Croparea Data
    const getFarmerPayload = (body: any) => {
        try {
            const regRecords = body?.message?.search_response?.[0]?.data?.reg_records;
            if (regRecords && (regRecords.FarmerData || regRecords.land_data)) {
                return regRecords;
            }
            return null;
        } catch (e) {
            return null;
        }
    };

    // Helper to extract WKT specifically
    const convertGeoJSONToWKT = (geometry: any) => {
        if (!geometry || !geometry.type || !geometry.coordinates) return null;
        
        try {
            if (geometry.type === 'Polygon') {
                const rings = geometry.coordinates.map((ring: any[]) => {
                    return `(${ring.map(coord => `${coord[0]} ${coord[1]}`).join(', ')})`;
                });
                return `POLYGON (${rings.join(', ')})`;
            } else if (geometry.type === 'MultiPolygon') {
                 const polygons = geometry.coordinates.map((polygon: any[]) => {
                     const rings = polygon.map((ring: any[]) => {
                         return `(${ring.map((coord: any[]) => `${coord[0]} ${coord[1]}`).join(', ')})`;
                     });
                     return `(${rings.join(', ')})`;
                 });
                 return `MULTIPOLYGON (${polygons.join(', ')})`;
            }
        } catch(e) {
             return null;
        }
        return null;
    };

    const fetchResponses = async () => {
        setIsLoading(true);
        setError(null);
        try {
            const res = await fetch(`/api/webhook-responses?type=${selectedType}&page=${page}&limit=50`);
            const data = await res.json();
            if (data.success) {
                setResponses(data.data);
                setTotalPages(data.totalPages || 1);
                setTotalCount(data.totalCount || data.data.length);
            } else {
                setError(data.error || 'Failed to fetch');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch responses');
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchResponses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedType, page]);

    const handleTypeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        setSelectedType(e.target.value);
        setPage(1);
    };

    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleString('en-IN', {
            day: '2-digit',
            month: 'short',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
    };

    const getEndpointPath = (source: string) => {
        if (source === 'agristack-farmers') return '/webhook/farmers/on-seek';
        if (source === 'agristack-kdss') return '/webhook/kdss/on-seek';
        return '/webhook/on-seek';
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="flex flex-col items-center gap-4">
                    <svg className="animate-spin w-8 h-8 text-green-600" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span className="text-stone-500">Loading webhook responses...</span>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
                <svg className="w-10 h-10 text-red-400 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-red-800 font-medium">{error}</p>
                <button onClick={fetchResponses} className="mt-4 px-4 py-2 bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors">
                    Retry
                </button>
            </div>
        );
    }

    const publicBase =
        String(process.env.NEXT_PUBLIC_APP_DOMAIN ?? '')
            .replace(/\/$/, '')
            .trim() || 'http://localhost:3000';
    const farmersUrl = `${publicBase}/webhook/farmers/on-seek`;
    const legacyUrl = `${publicBase}/webhook/on-seek`;
    const kdssUrl = `${publicBase}/webhook/kdss/on-seek`;

    return (
        <div className="space-y-4">
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                <p className="font-semibold text-amber-900">Webhook checklist</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-amber-900/90">
                    <li>
                        <strong>Seek (i1004:o1007)</strong> uses <code className="rounded bg-amber-100 px-1">sender_uri</code>{' '}
                        → <strong>Farmers</strong> URL below. Use filter <strong>“All”</strong> or <strong>“Farmers”</strong> — not
                        Legacy.
                    </li>
                    <li>
                        <strong>Webhook base:</strong> prefer the Mumbai Lambda URL in{' '}
                        <code className="rounded bg-amber-100 px-1">NEXT_PUBLIC_APP_DOMAIN</code>
                        {' '}(no Cloudflare tunnel needed). Legacy note: a{' '}
                        <code className="rounded bg-amber-100 px-1">*.trycloudflare.com</code>{' '}
                        host only works while <code className="rounded bg-amber-100 px-1">cloudflared</code> is running. If the URL
                        changed, update <code className="rounded bg-amber-100 px-1">NEXT_PUBLIC_APP_DOMAIN</code> in{' '}
                        <code className="rounded bg-amber-100 px-1">.env.local</code> and restart{' '}
                        <code className="rounded bg-amber-100 px-1">npm run dev</code>.
                    </li>
                    <li>
                        Health (GET):{' '}
                        <span className="break-all font-mono text-xs">{farmersUrl}</span>
                    </li>
                </ul>
                <div className="mt-3 space-y-1 border-t border-amber-200/80 pt-3 font-mono text-xs text-amber-900/85 break-all">
                    <div>
                        <span className="font-sans font-semibold">Farmers (Seek):</span> {farmersUrl}
                    </div>
                    <div>
                        <span className="font-sans font-semibold">Legacy (land / farmer-id):</span> {legacyUrl}
                    </div>
                    <div>
                        <span className="font-sans font-semibold">Krishi DSS:</span> {kdssUrl}
                    </div>
                </div>
            </div>

            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <h2 className="text-xl font-semibold text-gray-800">Webhook Responses</h2>
                    {totalCount > 0 && (
                        <span className="bg-green-100 text-green-800 text-sm font-medium px-2.5 py-0.5 rounded-full">
                            Total: {totalCount}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-3">
                    <select
                        value={selectedType}
                        onChange={handleTypeChange}
                        className="bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-green-500 focus:border-green-500 p-2.5 outline-none font-medium appearance-none"
                        style={{ paddingRight: '2.5rem', backgroundImage: 'url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'2\' stroke-linecap=\'round\' stroke-linejoin=\'round\'%3e%3cpolyline points=\'6 9 12 15 18 9\'%3e%3c/polyline%3e%3c/svg%3e")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 0.75rem center', backgroundSize: '1em' }}
                    >
                        <option value="all">All Webhooks</option>
                        <option value="farmers">Farmers — Seek i1004:o1007 callbacks</option>
                        <option value="kdss">Krishi DSS (on-seek)</option>
                        <option value="legacy">Legacy — farmer-land / land-parcel only</option>
                    </select>

                    <button
                        onClick={fetchResponses}
                        className="flex items-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors font-medium"
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                        Refresh
                    </button>
                </div>
            </div>

            {/* Empty State */}
            {responses.length === 0 && (
                <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
                    <svg className="w-16 h-16 text-stone-700 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
                    </svg>
                    <p className="text-stone-500 text-lg">No webhook responses yet</p>
                    <p className="text-stone-500 text-sm mt-1">Responses from AgriStack will appear here</p>
                </div>
            )}

            {/* Response Cards */}
            {responses.map((item) => (
                <div key={item._id} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                    {/* Card Header */}
                    <button
                        onClick={() => setExpandedId(expandedId === item._id ? null : item._id)}
                        className="w-full flex items-center justify-between px-5 py-4 hover:bg-gray-50 transition-colors"
                    >
                        <div className="flex items-center gap-4">
                            <span className="bg-blue-100 text-blue-800 text-xs font-bold px-2 py-1 rounded">
                                POST
                            </span>
                            <span className="font-medium text-gray-800">
                                {getEndpointPath(item.source)}
                            </span>
                            <span className="text-sm text-stone-500">{formatDate(item.receivedAt)}</span>
                        </div>
                        <div className="flex items-center gap-3">
                            <span className="text-xs text-stone-500 font-mono">{item._id}</span>
                            <svg
                                className={`w-5 h-5 text-stone-500 transition-transform ${expandedId === item._id ? 'rotate-180' : ''}`}
                                fill="none" stroke="currentColor" viewBox="0 0 24 24"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                            </svg>
                        </div>
                    </button>

                    {/* Expanded Body */}
                    {expandedId === item._id && (
                        <div className="border-t border-gray-200 p-4">
                            {(() => {
                                const payload = getFarmerPayload(item.body);
                                const hasData = payload !== null;

                                return (
                                    <div className="flex flex-col gap-4">
                                        {/* Toggle Switch */}
                                        {hasData && (
                                            <div className="flex bg-gray-100 p-1 rounded-lg self-start">
                                                <button 
                                                    onClick={() => setViewMode('table')}
                                                    className={`px-4 py-1.5 text-sm font-medium rounded-md transition-shadow ${viewMode === 'table' ? 'bg-white shadow-sm text-gray-800' : 'text-stone-500 hover:text-gray-700'}`}
                                                >
                                                    Table View
                                                </button>
                                                <button 
                                                    onClick={() => setViewMode('json')}
                                                    className={`px-4 py-1.5 text-sm font-medium rounded-md transition-shadow ${viewMode === 'json' ? 'bg-white shadow-sm text-gray-800' : 'text-stone-500 hover:text-gray-700'}`}
                                                >
                                                    Raw JSON
                                                </button>
                                            </div>
                                        )}

                                        {/* Display Area */}
                                        {(!hasData || viewMode === 'json') ? (
                                            <div className="bg-[#1e1e1e] rounded-lg p-4 overflow-x-auto max-h-[500px] overflow-y-auto custom-scrollbar">
                                                <pre
                                                    className="text-sm font-mono whitespace-pre-wrap"
                                                    style={{
                                                        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", Consolas, source-code-pro, monospace',
                                                        color: '#d4d4d4',
                                                    }}
                                                    dangerouslySetInnerHTML={{
                                                        __html: highlightJson(JSON.stringify(item.body, null, 2))
                                                    }}
                                                />
                                            </div>
                                        ) : (
                                            <div className="space-y-6 max-h-[600px] overflow-y-auto custom-scrollbar pr-2">
                                                {/* Farmer Data Table */}
                                                {payload.FarmerData && (
                                                    <div>
                                                        <h3 className="text-lg font-semibold text-gray-800 mb-3 border-b pb-2">Farmer Profile</h3>
                                                        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                                                            <table className="w-full text-sm text-left text-stone-400">
                                                                <tbody>
                                                                    {Object.entries(payload.FarmerData).filter(([k]) => !k.includes('hash')).map(([key, value]) => (
                                                                        <tr key={key} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                                                                            <th className="px-4 py-3 font-medium text-gray-900 bg-gray-50 w-1/3 capitalize border-r border-gray-100">
                                                                                {key.replace(/_/g, ' ')}
                                                                            </th>
                                                                            <td className="px-4 py-3">
                                                                                {String(value || '-')}
                                                                            </td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    </div>
                                                )}

                                                {/* Land Data Table */}
                                                {payload.land_data && Array.isArray(payload.land_data) && payload.land_data.length > 0 && (
                                                    <div>
                                                        <h3 className="text-lg font-semibold text-gray-800 mb-3 border-b pb-2">Land Records ({payload.land_data.length})</h3>
                                                        <div className="overflow-x-auto border border-gray-200 rounded-lg">
                                                            <table className="w-full text-sm text-left text-stone-400">
                                                                <thead className="bg-gray-50 border-b border-gray-200">
                                                                    <tr>
                                                                        <th className="px-4 py-3 font-medium text-gray-900 border-r border-gray-100">Farm ID</th>
                                                                        <th className="px-4 py-3 font-medium text-gray-900 border-r border-gray-100">Survey No</th>
                                                                        <th className="px-4 py-3 font-medium text-gray-900 border-r border-gray-100">Area</th>
                                                                        <th className="px-4 py-3 font-medium text-gray-900 border-r border-gray-100">Joint Owners</th>
                                                                        <th className="px-4 py-3 font-medium text-gray-900">Map Layout</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {payload.land_data.map((land: any, idx: number) => {
                                                                        const owners = land.joint_owners && Array.isArray(land.joint_owners) 
                                                                            ? land.joint_owners.map((o: any) => o.owner_name_ror).join(', ') 
                                                                            : land.owner_name_ror || '-';
                                                                            
                                                                        const wkt = convertGeoJSONToWKT(land.plot_geometry);
                                                                            
                                                                        return (
                                                                            <tr key={idx} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                                                                                <td className="px-4 py-3 font-mono text-xs text-gray-800 border-r border-gray-100">{land.farm_id || '-'}</td>
                                                                                <td className="px-4 py-3 border-r border-gray-100">{land.survey_number || '-'}</td>
                                                                                <td className="px-4 py-3 border-r border-gray-100 whitespace-nowrap">{land.owner_extent || '-'} {land.area_unit || ''}</td>
                                                                                <td className="px-4 py-3 border-r border-gray-100">{owners}</td>
                                                                                <td className="px-4 py-3">
                                                                                    {wkt ? (
                                                                                        <div className="max-w-xs max-h-24 overflow-y-auto w-full text-[10px] bg-gray-50 p-2 border border-gray-200 rounded font-mono break-all custom-scrollbar text-gray-700">
                                                                                            {wkt}
                                                                                        </div>
                                                                                    ) : <span className="text-stone-500 text-xs">Missing</span>}
                                                                                </td>
                                                                            </tr>
                                                                        );
                                                                    })}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                );
                            })()}
                        </div>
                    )}
                </div>
            ))}

            {/* Pagination Controls */}
            {totalPages > 1 && (
                <div className="flex items-center justify-between border-t border-gray-200 pt-6 pb-2">
                    <div className="text-sm text-stone-500">
                        Showing <span className="font-medium">{(page - 1) * 50 + 1}</span> to <span className="font-medium">{Math.min(page * 50, totalCount)}</span> of <span className="font-medium">{totalCount}</span> records
                    </div>
                    <div className="flex gap-2">
                        <button
                            onClick={() => setPage(Math.max(1, page - 1))}
                            disabled={page === 1}
                            className="px-3 py-1.5 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Previous
                        </button>
                        <div className="flex items-center justify-center min-w-[6rem] px-2 text-sm font-medium text-gray-700 bg-gray-50 rounded border border-gray-200">
                            Page {page} of {totalPages}
                        </div>
                        <button
                            onClick={() => setPage(Math.min(totalPages, page + 1))}
                            disabled={page === totalPages}
                            className="px-3 py-1.5 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Next
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
