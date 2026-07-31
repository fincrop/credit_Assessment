'use client';

import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth, type AuthUser } from '../components/providers/AuthProvider';

type Mode = 'signin' | 'signup';

function LoginForm() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const { setSessionUser } = useAuth();

    const [mode, setMode] = useState<Mode>('signin');
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);

    const callbackUrl = searchParams.get('callbackUrl') || '/';

    const switchMode = (next: Mode) => {
        setMode(next);
        setError('');
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setIsLoading(true);

        try {
            const endpoint = mode === 'signin' ? '/api/login' : '/api/signup';
            const body =
                mode === 'signin'
                    ? { email, password }
                    : { name, email, password };

            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify(body),
            });

            const data = await res.json().catch(() => ({}));

            if (!res.ok) {
                setError(
                    data.error ||
                        (mode === 'signin'
                            ? 'Invalid email or password'
                            : 'Could not create account')
                );
                return;
            }

            const user = data.user as AuthUser | undefined;
            if (user) {
                setSessionUser(user);
            }

            router.push(callbackUrl.startsWith('/') ? callbackUrl : '/');
            router.refresh();
        } catch {
            setError('Something went wrong. Please try again.');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="w-full max-w-md">
            <div className="text-center mb-8">
                <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-green-500 to-green-700 rounded-2xl mb-4 shadow-lg shadow-green-200">
                    <span className="text-white text-2xl font-bold">A</span>
                </div>
                <h1 className="text-3xl font-bold text-gray-900">AgriStack</h1>
                <p className="text-stone-500 mt-1">API Sandbox</p>
            </div>

            <div className="bg-white rounded-2xl shadow-xl shadow-gray-200/50 border border-gray-100 p-8">
                <div className="flex rounded-lg bg-gray-100 p-1 mb-6">
                    <button
                        type="button"
                        onClick={() => switchMode('signin')}
                        className={`flex-1 py-2 text-sm font-semibold rounded-md transition-all ${
                            mode === 'signin'
                                ? 'bg-white text-gray-900 shadow-sm'
                                : 'text-stone-500 hover:text-gray-700'
                        }`}
                    >
                        Sign in
                    </button>
                    <button
                        type="button"
                        onClick={() => switchMode('signup')}
                        className={`flex-1 py-2 text-sm font-semibold rounded-md transition-all ${
                            mode === 'signup'
                                ? 'bg-white text-gray-900 shadow-sm'
                                : 'text-stone-500 hover:text-gray-700'
                        }`}
                    >
                        Sign up
                    </button>
                </div>

                <h2 className="text-xl font-semibold text-gray-800 mb-6">
                    {mode === 'signin'
                        ? 'Sign in to your account'
                        : 'Create a new account'}
                </h2>

                {error && (
                    <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6 flex items-center gap-2">
                        <svg
                            className="w-5 h-5 text-red-500 shrink-0"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                            />
                        </svg>
                        <span className="text-red-700 text-sm">{error}</span>
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-5">
                    {mode === 'signup' && (
                        <div>
                            <label
                                htmlFor="name"
                                className="block text-sm font-medium text-gray-700 mb-1.5"
                            >
                                Full name
                            </label>
                            <input
                                id="name"
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                required
                                autoComplete="name"
                                placeholder="Your name"
                                className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:border-green-500 focus:ring-2 focus:ring-green-200 outline-none transition-all text-gray-800 placeholder-gray-400"
                            />
                        </div>
                    )}

                    <div>
                        <label
                            htmlFor="email"
                            className="block text-sm font-medium text-gray-700 mb-1.5"
                        >
                            Email Address
                        </label>
                        <input
                            id="email"
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                            autoComplete="email"
                            placeholder="you@example.com"
                            className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:border-green-500 focus:ring-2 focus:ring-green-200 outline-none transition-all text-gray-800 placeholder-gray-400"
                        />
                    </div>

                    <div>
                        <label
                            htmlFor="password"
                            className="block text-sm font-medium text-gray-700 mb-1.5"
                        >
                            Password
                        </label>
                        <input
                            id="password"
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                            minLength={mode === 'signup' ? 6 : 1}
                            autoComplete={
                                mode === 'signin'
                                    ? 'current-password'
                                    : 'new-password'
                            }
                            placeholder={
                                mode === 'signup'
                                    ? 'At least 6 characters'
                                    : '••••••••'
                            }
                            className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:border-green-500 focus:ring-2 focus:ring-green-200 outline-none transition-all text-gray-800 placeholder-gray-400"
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={isLoading}
                        className={`w-full py-3 rounded-lg font-semibold text-white transition-all ${
                            isLoading
                                ? 'bg-gray-400 cursor-not-allowed'
                                : 'bg-gradient-to-r from-green-600 to-green-700 hover:from-green-700 hover:to-green-800 shadow-lg shadow-green-200 hover:shadow-green-300'
                        }`}
                    >
                        {isLoading ? (
                            <span className="flex items-center justify-center gap-2">
                                <svg
                                    className="animate-spin w-5 h-5"
                                    viewBox="0 0 24 24"
                                >
                                    <circle
                                        className="opacity-25"
                                        cx="12"
                                        cy="12"
                                        r="10"
                                        stroke="currentColor"
                                        strokeWidth="4"
                                        fill="none"
                                    />
                                    <path
                                        className="opacity-75"
                                        fill="currentColor"
                                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                                    />
                                </svg>
                                {mode === 'signin'
                                    ? 'Signing in...'
                                    : 'Creating account...'}
                            </span>
                        ) : mode === 'signin' ? (
                            'Sign In'
                        ) : (
                            'Create account'
                        )}
                    </button>
                </form>
            </div>

            <p className="text-center text-stone-500 text-sm mt-6">
                AgriStack Sandbox &copy; {new Date().getFullYear()}
            </p>
        </div>
    );
}

export default function LoginPage() {
    return (
        <div className="min-h-screen bg-gradient-to-br from-green-50 via-white to-green-50 flex items-center justify-center p-4">
            <Suspense
                fallback={
                    <div className="text-stone-500 text-sm">Loading…</div>
                }
            >
                <LoginForm />
            </Suspense>
        </div>
    );
}
