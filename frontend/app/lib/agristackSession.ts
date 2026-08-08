/**
 * Browser-session AgriStack credentials (sessionStorage).
 * Cleared on tab close and on AgriCredit logout — never use server env defaults.
 */

export const AGRI_CREDS_KEY = 'agristack_session_creds';
export const AGRI_TOKEN_KEY = 'agristack_access_token';

export type AgriSessionCreds = {
  username: string;
  password: string;
  client_id: string;
};

export function readSessionCreds(): AgriSessionCreds | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = sessionStorage.getItem(AGRI_CREDS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AgriSessionCreds;
    if (!parsed?.username || !parsed?.password) return null;
    return {
      username: String(parsed.username),
      password: String(parsed.password),
      client_id: String(parsed.client_id || 'registry_sandbox'),
    };
  } catch {
    return null;
  }
}

export function writeSessionCreds(creds: AgriSessionCreds) {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(
      AGRI_CREDS_KEY,
      JSON.stringify({
        username: creds.username,
        password: creds.password,
        client_id: creds.client_id || 'registry_sandbox',
      })
    );
  } catch {
    /* ignore quota */
  }
}

export function readSessionAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = sessionStorage.getItem(AGRI_TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { access_token?: string };
    return parsed?.access_token ? String(parsed.access_token) : null;
  } catch {
    return null;
  }
}

export function writeSessionAccessToken(token: {
  access_token: string;
  token_type?: string;
  expires_in?: number;
  refresh_token?: string;
}) {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(
      AGRI_TOKEN_KEY,
      JSON.stringify({
        access_token: token.access_token,
        token_type: token.token_type || 'Bearer',
        expires_in: token.expires_in,
        refresh_token: token.refresh_token,
      })
    );
  } catch {
    /* ignore quota */
  }
}

export function clearAgriStackSession() {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.removeItem(AGRI_CREDS_KEY);
    sessionStorage.removeItem(AGRI_TOKEN_KEY);
    sessionStorage.removeItem('agristack_last_saved_farmers');
  } catch {
    /* ignore */
  }
}

/** Validated AgriStack session for this browser tab. */
export function hasAgriStackSession(): boolean {
  return Boolean(readSessionAccessToken() && readSessionCreds());
}
