import { SignJWT, jwtVerify } from 'jose';

function resolveAuthSecret(): Uint8Array {
    const raw = (
        process.env.AUTH_SECRET ||
        process.env.NEXTAUTH_SECRET ||
        ''
    ).trim();
    if (raw) {
        return new TextEncoder().encode(raw);
    }
    if (process.env.NODE_ENV === 'production') {
        throw new Error(
            'AUTH_SECRET (or NEXTAUTH_SECRET) must be set in production'
        );
    }
    // Local/dev only — never used when NODE_ENV=production
    return new TextEncoder().encode('dev-only-insecure-auth-secret');
}

const SECRET = resolveAuthSecret();

export interface JWTPayload {
    id: string;
    email: string;
    name: string;
    role: string;
}

export async function signJWT(payload: JWTPayload): Promise<string> {
    return new SignJWT({ ...payload })
        .setProtectedHeader({ alg: 'HS256' })
        .setIssuedAt()
        .setExpirationTime('7d')
        .sign(SECRET);
}

export async function verifyJWT(token: string): Promise<JWTPayload | null> {
    try {
        const { payload } = await jwtVerify(token, SECRET);
        return payload as unknown as JWTPayload;
    } catch {
        return null;
    }
}
