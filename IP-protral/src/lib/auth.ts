import { randomBytes } from 'crypto';
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import type { QueryResultRow } from 'pg';
import { ensureDatabaseReady } from './db-init';
import { pgQuery } from './postgres';
import type { AuthUser } from './types';

export const AUTH_COOKIE_NAME = 'patent_auth_session';

interface SessionUserRow extends QueryResultRow {
  id: number;
  name: string;
  email: string;
  role: AuthUser['role'];
  status: AuthUser['status'];
  approved_at: string | null;
  created_at: string | null;
}

function getSessionTtlDays(): number {
  const value = Number(process.env.AUTH_SESSION_TTL_DAYS || 7);
  if (!Number.isFinite(value) || value <= 0) {
    return 7;
  }
  return value;
}

function mapUser(row: SessionUserRow): AuthUser {
  return {
    id: row.id,
    name: row.name,
    email: row.email,
    role: row.role,
    status: row.status,
    approvedAt: row.approved_at,
    createdAt: row.created_at,
  };
}

export function getAuthCookieValue(request: NextRequest): string | null {
  return request.cookies.get(AUTH_COOKIE_NAME)?.value || null;
}

export async function createAuthSession(userId: number): Promise<{ token: string; expiresAt: Date }> {
  await ensureDatabaseReady();
  const token = randomBytes(32).toString('hex');
  const expiresAt = new Date(Date.now() + getSessionTtlDays() * 24 * 60 * 60 * 1000);

  await pgQuery(
    `
      insert into auth_sessions (id, user_id, expires_at, last_seen_at)
      values ($1, $2, $3, now())
    `,
    [token, userId, expiresAt.toISOString()],
  );

  return { token, expiresAt };
}

export async function deleteAuthSession(token: string): Promise<void> {
  await ensureDatabaseReady();
  await pgQuery('delete from auth_sessions where id = $1', [token]);
}

export async function getCurrentUserBySessionToken(token: string | null): Promise<AuthUser | null> {
  if (!token) {
    return null;
  }

  await ensureDatabaseReady();
  const result = await pgQuery<SessionUserRow>(
    `
      select
        u.id,
        u.name,
        u.email,
        u.role,
        u.status,
        u.approved_at,
        u.created_at
      from auth_sessions s
      join users u on u.id = s.user_id
      where s.id = $1
        and s.expires_at > now()
      limit 1
    `,
    [token],
  );

  const row = result.rows[0];
  if (!row) {
    return null;
  }

  await pgQuery('update auth_sessions set last_seen_at = now() where id = $1', [token]);
  return mapUser(row);
}

export async function getCurrentUserFromRequest(request: NextRequest): Promise<AuthUser | null> {
  return getCurrentUserBySessionToken(getAuthCookieValue(request));
}

export function setAuthCookie(response: NextResponse, token: string, expiresAt: Date): void {
  response.cookies.set(AUTH_COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    expires: expiresAt,
  });
}

export function clearAuthCookie(response: NextResponse): void {
  response.cookies.set(AUTH_COOKIE_NAME, '', {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 0,
  });
}

export function createUnauthorizedResponse(request: NextRequest, message: string = '请先登录'): NextResponse {
  const response = NextResponse.json({ error: message }, { status: 401 });
  if (getAuthCookieValue(request)) {
    clearAuthCookie(response);
  }
  return response;
}

export function isAdmin(user: AuthUser | null): boolean {
  return user?.role === 'admin';
}
