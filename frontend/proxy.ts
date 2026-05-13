import { NextRequest, NextResponse } from 'next/server';
import { verifyJWT } from './app/lib/jwt';

export async function proxy(request: NextRequest) {
    const { pathname } = request.nextUrl;

    // Allow these paths without authentication
    const publicPaths = ['/login', '/api/login', '/api/logout', '/webhook'];
    const isPublicPath = publicPaths.some((path) => pathname.startsWith(path));

    if (isPublicPath) {
        return NextResponse.next();
    }

    // Check for auth token cookie
    const token = request.cookies.get('auth-token')?.value;

    if (!token) {
        const loginUrl = new URL('/login', request.url);
        loginUrl.searchParams.set('callbackUrl', pathname);
        return NextResponse.redirect(loginUrl);
    }

    const payload = await verifyJWT(token);

    if (!payload) {
        const loginUrl = new URL('/login', request.url);
        loginUrl.searchParams.set('callbackUrl', pathname);
        return NextResponse.redirect(loginUrl);
    }

    return NextResponse.next();
}

export const config = {
    matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
