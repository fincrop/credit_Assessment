import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface TokenState {
    accessToken: string | null;
    tokenType: string | null;
    expiresIn: number | null;
    refreshToken: string | null;
}

const initialState: TokenState = {
    accessToken: null,
    tokenType: null,
    expiresIn: null,
    refreshToken: null,
};

const tokenSlice = createSlice({
    name: 'token',
    initialState,
    reducers: {
        setToken: (state, action: PayloadAction<{
            access_token: string;
            token_type?: string;
            expires_in?: number;
            refresh_token?: string;
        }>) => {
            state.accessToken = action.payload.access_token;
            state.tokenType = action.payload.token_type || 'Bearer';
            state.expiresIn = action.payload.expires_in || null;
            state.refreshToken = action.payload.refresh_token || null;
        },
        clearToken: (state) => {
            state.accessToken = null;
            state.tokenType = null;
            state.expiresIn = null;
            state.refreshToken = null;
        },
    },
});

export const { setToken, clearToken } = tokenSlice.actions;
export default tokenSlice.reducer;
