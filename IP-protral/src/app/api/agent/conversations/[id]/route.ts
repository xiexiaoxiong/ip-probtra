import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import { getPatentAgentConversation } from '@/lib/patent-agent-store';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  const { id } = await params;
  const conversation = await getPatentAgentConversation(id, user);
  if (!conversation) {
    return NextResponse.json({ error: '对话不存在或无权访问' }, { status: 404 });
  }
  return NextResponse.json(
    { conversation },
    { headers: { 'Cache-Control': 'no-store, no-cache, must-revalidate' } },
  );
}
