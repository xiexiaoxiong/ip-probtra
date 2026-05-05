import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';

export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return NextResponse.json({ error: '请先登录' }, { status: 401 });
  }

  const { id } = await params;
  const session = await getSessionAsync(id);

  if (!session) {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权访问该分析会话' }, { status: 403 });
  }

  return NextResponse.json({ session });
}
