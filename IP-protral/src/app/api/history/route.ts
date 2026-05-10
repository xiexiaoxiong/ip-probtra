import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import { listSessionsForUser } from '@/lib/analysis-store';

export async function GET(request: NextRequest) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return createUnauthorizedResponse(request);
  }

  const sessions = await listSessionsForUser(currentUser);
  return NextResponse.json({ sessions });
}
