import { NextRequest, NextResponse } from 'next/server';
import { clearAuthCookie, deleteAuthSession, getAuthCookieValue } from '@/lib/auth';

export async function POST(request: NextRequest) {
  const token = getAuthCookieValue(request);
  if (token) {
    await deleteAuthSession(token);
  }

  const response = NextResponse.json({ success: true });
  clearAuthCookie(response);
  return response;
}
