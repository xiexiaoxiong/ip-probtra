import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { listErrorReports } from '@/lib/error-reports-store';

export async function GET(request: NextRequest) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!isAdmin(currentUser)) {
    return NextResponse.json({ error: '无权限访问管理员接口' }, { status: 403 });
  }

  const limit = Number(request.nextUrl.searchParams.get('limit') || '50');
  const offset = Number(request.nextUrl.searchParams.get('offset') || '0');
  const { reports } = await listErrorReports({
    limit: Number.isFinite(limit) ? limit : 50,
    offset: Number.isFinite(offset) ? offset : 0,
  });

  return NextResponse.json({ reports });
}

