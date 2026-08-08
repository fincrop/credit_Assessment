'use client';

import { useState, FormEvent } from 'react';
import Link from 'next/link';
import {
  writeSessionCreds,
  writeSessionAccessToken,
  type AgriSessionCreds,
} from '../lib/agristackSession';

type TokenPayload = {
  access_token?: string;
  token_type?: string;
  expires_in?: number;
  refresh_token?: string;
};

export type AgriStackLoginFormProps = {
  /** Called after AgriStack token validates and session is stored. */
  onSuccess: (creds: AgriSessionCreds, token: TokenPayload) => void;
  title?: string;
  subtitle?: string;
  showHomeLink?: boolean;
};

export function AgriStackLoginForm({
  onSuccess,
  title = 'Sign in to AgriStack',
  subtitle = 'Enter your AgriStack sandbox credentials. These are separate from your AgriCredit account and are only kept for this browser session.',
  showHomeLink = true,
}: AgriStackLoginFormProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [clientId, setClientId] = useState('registry_sandbox');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const u = username.trim();
      const cid = clientId.trim() || 'registry_sandbox';
      const res = await fetch('/api/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          username: u,
          password,
          client_id: cid,
          grant_type: 'password',
        }),
      });
      const result = await res.json();
      const tokenData = result?.data as TokenPayload | undefined;
      if (result.success && tokenData?.access_token) {
        const creds: AgriSessionCreds = {
          username: u,
          password,
          client_id: cid,
        };
        writeSessionCreds(creds);
        writeSessionAccessToken({
          access_token: tokenData.access_token,
          token_type: tokenData.token_type,
          expires_in: tokenData.expires_in,
          refresh_token: tokenData.refresh_token,
        });
        onSuccess(creds, tokenData);
        setPassword('');
      } else {
        setError(result?.error?.message || 'Provide valid AgriStack credentials');
      }
    } catch {
      setError('Could not reach AgriStack token service. Try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative z-10 w-full max-w-md">
      {showHomeLink && (
        <div className="mb-6 flex items-center justify-between">
          <Link href="/" className="text-sm text-stone-500 hover:text-emerald-700 transition-colors">
            ← Platform home
          </Link>
        </div>
      )}
      <div className="bg-white/90 border border-[#E4DFD4] rounded-2xl p-8 shadow-sm">
        <div className="mb-6">
          <p className="text-[10px] font-mono text-sky-600 uppercase tracking-wider mb-1">
            AgriStack path
          </p>
          <h1 className="text-2xl font-bold text-stone-900 tracking-tight">{title}</h1>
          <p className="text-sm text-stone-500 mt-2 leading-relaxed">{subtitle}</p>
        </div>
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-5 text-sm text-red-700">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="agri-username" className="block text-sm font-medium text-stone-700 mb-1.5">
              Username
            </label>
            <input
              id="agri-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
              className="w-full px-4 py-2.5 rounded-lg border border-stone-300 focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none text-stone-800"
            />
          </div>
          <div>
            <label htmlFor="agri-password" className="block text-sm font-medium text-stone-700 mb-1.5">
              Password
            </label>
            <input
              id="agri-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              className="w-full px-4 py-2.5 rounded-lg border border-stone-300 focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none text-stone-800"
            />
          </div>
          <div>
            <label htmlFor="agri-client-id" className="block text-sm font-medium text-stone-700 mb-1.5">
              Client ID
            </label>
            <input
              id="agri-client-id"
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg border border-stone-300 focus:border-sky-500 focus:ring-2 focus:ring-sky-200 outline-none text-stone-800 font-mono text-sm"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg font-semibold text-white bg-sky-600 hover:bg-sky-500 disabled:bg-stone-400 transition-colors"
          >
            {loading ? 'Validating…' : 'Validate & continue'}
          </button>
        </form>
        <p className="text-xs text-stone-500 mt-5 leading-relaxed">
          No AgriStack account? Use the{' '}
          <Link href="/farmer" className="text-emerald-700 font-medium hover:underline">
            Farmer Assessment Journey
          </Link>{' '}
          instead — it does not require AgriStack credentials.
        </p>
      </div>
    </div>
  );
}

export function AgriStackLoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#F5F2EB] text-stone-800 relative overflow-hidden flex items-center justify-center p-4">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 70% 45% at 15% 0%, rgba(56,189,248,0.16), transparent 55%), linear-gradient(180deg, #F5F2EB 0%, #EFEBE3 100%)',
        }}
      />
      {children}
    </div>
  );
}
