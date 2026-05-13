'use client';

import { useState } from 'react';
import DataTable from './DataTable';

interface ResponseSectionProps {
    response: {
        success: boolean;
        data?: unknown;
        error?: {
            code: number;
            message: string;
        };
        statusCode: number;
        responseTime?: number;
    } | null;
    isLoading: boolean;
}

// Syntax highlighting function for JSON with inline styles
function highlightJson(json: string): string {
    // Escape HTML first
    const escaped = json
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Apply syntax highlighting with inline styles
    return escaped
        // Strings (will be re-processed for keys)
        .replace(/"([^"\\]*(\\.[^"\\]*)*)"/g, (match, content) => {
            return `"<span style="color: #ce9178">${content}</span>"`;
        })
        // Numbers
        .replace(/\b(-?\d+\.?\d*)\b/g, '<span style="color: #b5cea8">$1</span>')
        // Booleans
        .replace(/\b(true|false)\b/g, '<span style="color: #569cd6">$1</span>')
        // Null
        .replace(/\bnull\b/g, '<span style="color: #569cd6">null</span>')
        // Keys (strings followed by colon) - override the string color for keys
        .replace(/"<span style="color: #ce9178">([^<]+)<\/span>"\s*:/g,
            '"<span style="color: #9cdcfe">$1</span>":')
        // Brackets and braces
        .replace(/([{}\[\]])/g, '<span style="color: #ffd700">$1</span>')
        // Colons after keys
        .replace(/(<\/span>"):/g, '$1<span style="color: #d4d4d4">:</span>')
        // Commas
        .replace(/,/g, '<span style="color: #d4d4d4">,</span>');
}

export default function ResponseSection({ response, isLoading }: ResponseSectionProps) {
    const [viewMode, setViewMode] = useState<'json' | 'table'>('json');

    if (isLoading) {
        return (
            <div className="bg-white rounded-lg border border-gray-200 p-6">
                <h2 className="text-xl font-semibold text-gray-800 mb-4">Response</h2>
                <div className="flex items-center justify-center py-12">
                    <div className="flex flex-col items-center gap-4">
                        <svg className="animate-spin w-8 h-8 text-green-600" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                        <span className="text-gray-500">Fetching response...</span>
                    </div>
                </div>
            </div>
        );
    }

    if (!response) {
        return (
            <div className="bg-white rounded-lg border border-gray-200 p-6">
                <h2 className="text-xl font-semibold text-gray-800 mb-4">Response</h2>
                <div className="bg-gray-50 rounded-lg p-8 text-center">
                    <svg className="w-12 h-12 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                    <p className="text-gray-500">Click &quot;RUN&quot; to execute the API and see the response here</p>
                </div>
            </div>
        );
    }

    // Extract table data from response
    const extractTableData = (data: unknown): Record<string, unknown>[] | null => {
        if (!data) return null;
        if (Array.isArray(data)) return data as Record<string, unknown>[];
        if (typeof data === 'object') {
            // Try to find arrays in the response
            const obj = data as Record<string, unknown>;
            for (const key of Object.keys(obj)) {
                if (Array.isArray(obj[key])) {
                    return obj[key] as Record<string, unknown>[];
                }
            }
            return [obj];
        }
        return null;
    };

    const tableData = extractTableData(response.data);
    const jsonString = JSON.stringify(response.data || response.error || response, null, 2);

    return (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-4">
                    <h2 className="text-xl font-semibold text-gray-800">Response</h2>
                    {response.responseTime && (
                        <span className="text-sm text-gray-500">
                            {response.responseTime}ms
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded text-sm font-medium ${response.statusCode >= 200 && response.statusCode < 300
                        ? 'bg-green-100 text-green-800'
                        : 'bg-red-100 text-red-800'
                        }`}>
                        {response.statusCode}
                    </span>
                </div>

                {/* View Toggle */}
                <div className="flex items-center gap-1 bg-gray-100 p-1 rounded-lg">
                    <button
                        onClick={() => setViewMode('json')}
                        className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${viewMode === 'json'
                            ? 'bg-white text-green-700 shadow-sm'
                            : 'text-gray-600 hover:text-gray-800'
                            }`}
                    >
                        JSON
                    </button>
                    <button
                        onClick={() => setViewMode('table')}
                        className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${viewMode === 'table'
                            ? 'bg-white text-green-700 shadow-sm'
                            : 'text-gray-600 hover:text-gray-800'
                            }`}
                    >
                        Table
                    </button>
                </div>
            </div>

            {/* Error Display */}
            {response.error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <div className="flex items-start gap-3">
                        <svg className="w-5 h-5 text-red-500 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <div>
                            <p className="font-semibold text-red-800">Error {response.error.code}</p>
                            <p className="text-red-600">{response.error.message}</p>
                        </div>
                    </div>
                </div>
            )}

            {/* Response Content */}
            {viewMode === 'json' ? (
                <div className="bg-[#1e1e1e] rounded-lg p-4 overflow-x-auto">
                    <pre
                        className="text-sm font-mono whitespace-pre-wrap"
                        style={{
                            fontFamily: 'Monaco, Menlo, "Ubuntu Mono", Consolas, source-code-pro, monospace',
                            color: '#d4d4d4',
                        }}
                        dangerouslySetInnerHTML={{ __html: highlightJson(jsonString) }}
                    />
                </div>
            ) : (
                tableData ? (
                    <DataTable data={tableData} />
                ) : (
                    <div className="text-center py-8 text-gray-500">
                        Unable to display data as table. Please use JSON view.
                    </div>
                )
            )}
        </div>
    );
}
