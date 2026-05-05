import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

const AUTH_COOKIE_NAME = 'patent_auth_session';

function hasAuthCookie(request: NextRequest): boolean {
  return Boolean(request.cookies.get(AUTH_COOKIE_NAME)?.value);
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const authenticated = hasAuthCookie(request);
  const isAuthPage = pathname === '/login' || pathname === '/register';
  const isProtectedPage =
    pathname === '/'
    || pathname.startsWith('/results')
    || pathname.startsWith('/database')
    || pathname.startsWith('/history')
    || pathname.startsWith('/admin');

  if (!authenticated && isProtectedPage) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('redirect', pathname);
    return NextResponse.redirect(url);
  }

  if (authenticated && isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = '/';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/', '/login', '/register', '/results/:path*', '/database/:path*', '/history/:path*', '/admin/:path*'],
};
