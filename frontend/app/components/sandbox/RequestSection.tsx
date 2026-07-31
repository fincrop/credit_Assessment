'use client';

import JsonEditor from './JsonEditor';

interface RequestSectionProps {
    headers: string;
    onHeadersChange: (headers: string) => void;
    requestBody: string;
    onRequestBodyChange: (body: string) => void;
    onRun: () => void;
    isLoading: boolean;
}

export default function RequestSection({
    headers,
    onHeadersChange,
    requestBody,
    onRequestBodyChange,
    onRun,
    isLoading,
}: RequestSectionProps) {
    return (
        <div className="space-y-6">
            {/* Request Title */}
            <h2 className="text-xl font-semibold text-gray-800">Request</h2>

            {/* Header Parameters */}
            <div className="bg-white rounded-lg border border-gray-200 p-4">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-gray-800">Header Parameters</h3>
                    <span className="text-sm text-stone-500">JSON format</span>
                </div>

                <JsonEditor value={headers} onChange={onHeadersChange} />
            </div>

            {/* Body */}
            <div className="bg-white rounded-lg border border-gray-200 p-4">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-gray-800">Body</h3>
                    <button
                        onClick={onRun}
                        disabled={isLoading}
                        className={`flex items-center gap-2 px-6 py-2 rounded-lg font-medium text-white transition-all ${isLoading
                                ? 'bg-gray-400 cursor-not-allowed'
                                : 'bg-green-600 hover:bg-green-700 shadow-lg hover:shadow-green-200'
                            }`}
                    >
                        {isLoading ? (
                            <>
                                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                </svg>
                                Running...
                            </>
                        ) : (
                            <>
                                RUN
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                                </svg>
                            </>
                        )}
                    </button>
                </div>

                <JsonEditor value={requestBody} onChange={onRequestBodyChange} />
            </div>
        </div>
    );
}
