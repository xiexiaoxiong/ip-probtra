import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest } from '@/lib/auth';
import { listSessionsForUser } from '@/lib/analysis-store';

export async function GET(request: NextRequest) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return NextResponse.json({ error: '请先登录' }, { status: 401 });
  }

  const sessions = await listSessionsForUser(currentUser);
  return NextResponse.json({ sessions });
}
