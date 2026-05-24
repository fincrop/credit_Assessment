'use client';

import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../hooks/useRedux';
import { clearToken, setToken } from '../../store/tokenSlice';
import {
    ConnectionMode,
    getConnectionMode,
    setConnectionMode,
    tokenCurlExample,
} from '../../lib/agristackClient';
import { getEndpointConfigs } from '../../config/endpoints';

export default function AgriStackConnectionPanel() {
    const dispatch = useAppDispatch();
    const accessToken = useAppSelector((state) => state.token.accessToken);
    const [mode, setMode] = useState<ConnectionMode>('auto');
    const [manualToken, setManualToken] = useState('');
    const [showHelp, setShowHelp] = useState(false);

    useEffect(() => {
        setMode(getConnectionMode());
    }, []);

    const tokenBody = getEndpointConfigs(null).token.body as Record<string, unknown>;
    const curl = tokenCurlExample(tokenBody);

    const applyMode = (next: ConnectionMode) => {
        setMode(next);
        setConnectionMode(next);
    };

    const applyManualToken = () => {
        const trimmed = manualToken.trim().replace(/^Bearer\s+/i, '');
        if (!trimmed) return;
        dispatch(setToken({ access_token: trimmed, token_type: 'Bearer' }));
        setManualToken('');
    };

    return (
        <div className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-gray-800">AgriStack connection</h3>
                    <p className="text-xs text-gray-500 mt-1 max-w-2xl">
                        Deployed apps call AgriStack from the <strong>cloud server IP</strong> (Render), not your Wi‑Fi.
                        The sandbox often returns <strong>403 Forbidden</strong> for datacenter IPs. Auto mode retries from your browser (your network IP).
                    </p>
                </div>
                {accessToken && (
                    <span className="text-xs font-medium text-green-700 bg-green-50 border border-green-200 px-2 py-1 rounded">
                        Token active
                    </span>
                )}
            </div>

            <div className="flex flex-wrap gap-2">
                {(['auto', 'browser', 'server'] as ConnectionMode[]).map((m) => (
                    <button
                        key={m}
                        type="button"
                        onClick={() => applyMode(m)}
                        className={`px-3 py-1.5 rounded-md text-xs font-semibold capitalize transition-colors ${
                            mode === m
                                ? 'bg-green-600 text-white'
                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                        }`}
                    >
                        {m === 'auto' ? 'Auto (recommended)' : m}
                    </button>
                ))}
            </div>

            <p className="text-xs text-gray-500">
                {mode === 'auto' && 'Server first; on 403, retry from your browser automatically.'}
                {mode === 'browser' && 'Always call AgriStack from your browser (best on deployed Render/Vercel).'}
                {mode === 'server' && 'Always use the server proxy (works on localhost; often blocked when deployed).'}
            </p>

            <div className="border-t border-gray-100 pt-3 space-y-2">
                <label htmlFor="manualToken" className="block text-xs font-semibold text-gray-700">
                    Paste access token (fallback)
                </label>
                <div className="flex flex-wrap gap-2">
                    <input
                        id="manualToken"
                        type="password"
                        value={manualToken}
                        onChange={(e) => setManualToken(e.target.value)}
                        placeholder="eyJhbGciOiJSUzI1NiIs..."
                        className="flex-1 min-w-[200px] text-xs font-mono border border-gray-300 rounded-md px-3 py-2"
                    />
                    <button
                        type="button"
                        onClick={applyManualToken}
                        className="px-3 py-2 text-xs font-semibold bg-gray-800 text-white rounded-md hover:bg-gray-700"
                    >
                        Use token
                    </button>
                    {accessToken && (
                        <button
                            type="button"
                            onClick={() => dispatch(clearToken())}
                            className="px-3 py-2 text-xs font-semibold text-red-600 border border-red-200 rounded-md hover:bg-red-50"
                        >
                            Clear
                        </button>
                    )}
                </div>
            </div>

            <button
                type="button"
                onClick={() => setShowHelp((v) => !v)}
                className="text-xs text-green-700 font-medium hover:underline"
            >
                {showHelp ? 'Hide' : 'Show'} local token command
            </button>
            {showHelp && (
                <pre className="text-[11px] font-mono bg-gray-900 text-gray-100 p-3 rounded-md overflow-x-auto whitespace-pre-wrap">
                    {curl}
                </pre>
            )}
        </div>
    );
}
