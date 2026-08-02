'use client';

import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useAppDispatch } from '../../hooks/useRedux';
import { clearToken } from '../../store/tokenSlice';

export interface AuthUser {
    id: string;
    email: string;
    name: string;
    role: string;
}

interface AuthContextType {
    user: AuthUser | null;
    loading: boolean;
    logout: () => Promise<void>;
    refreshUser: () => Promise<AuthUser | null>;
    setSessionUser: (user: AuthUser | null) => void;
}

const AuthContext = createContext<AuthContextType>({
    user: null,
    loading: true,
    logout: async () => {},
    refreshUser: async () => null,
    setSessionUser: () => {},
});

export function useAuth() {
    return useContext(AuthContext);
}

function clearAgriStackSession() {
    try {
        sessionStorage.removeItem('agristack_access_token');
        sessionStorage.removeItem('agristack_session_creds');
    } catch {
        /* ignore */
    }
}

export default function AuthProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<AuthUser | null>(null);
    const [loading, setLoading] = useState(true);
    const router = useRouter();
    const dispatch = useAppDispatch();

    const refreshUser = useCallback(async (): Promise<AuthUser | null> => {
        try {
            const res = await fetch('/api/me', { credentials: 'include' });
            const data = res.ok ? await res.json() : null;
            const next = (data?.user as AuthUser | null) || null;
            setUser(next);
            return next;
        } catch {
            setUser(null);
            return null;
        } finally {
            setLoading(false);
        }
    }, []);

    const setSessionUser = useCallback((next: AuthUser | null) => {
        setUser(next);
        setLoading(false);
    }, []);

    useEffect(() => {
        void refreshUser();
    }, [refreshUser]);

    const logout = useCallback(async () => {
        await fetch('/api/logout', { method: 'POST', credentials: 'include' });
        clearAgriStackSession();
        dispatch(clearToken());
        setUser(null);
        router.push('/login');
    }, [router, dispatch]);

    return (
        <AuthContext.Provider value={{ user, loading, logout, refreshUser, setSessionUser }}>
            {children}
        </AuthContext.Provider>
    );
}
