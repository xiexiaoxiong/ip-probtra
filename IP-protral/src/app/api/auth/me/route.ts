import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getAuthCookieValue, getCurrentUserFromRequest } from '@/lib/auth';

export async function GET(request: NextRequest) {
  const user = await getCurrentUserFromRequest(request);
  if (!user && getAuthCookieValue(request)) {
    return createUnauthorizedResponse(request);
  }
  return NextResponse.json({ user });
}
