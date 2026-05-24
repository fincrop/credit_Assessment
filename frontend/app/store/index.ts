import { configureStore } from '@reduxjs/toolkit';
import tokenReducer, { TOKEN_STORAGE_KEY, TokenState } from './tokenSlice';

function loadPersistedToken(): TokenState | undefined {
    if (typeof window === 'undefined') return undefined;
    try {
        const raw = sessionStorage.getItem(TOKEN_STORAGE_KEY);
        if (!raw) return undefined;
        return JSON.parse(raw) as TokenState;
    } catch {
        return undefined;
    }
}

const persisted = loadPersistedToken();

export const store = configureStore({
    reducer: {
        token: tokenReducer,
    },
    preloadedState: persisted ? { token: persisted } : undefined,
});

if (typeof window !== 'undefined') {
    store.subscribe(() => {
        try {
            sessionStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(store.getState().token));
        } catch {
            // ignore quota / private mode
        }
    });
}

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
