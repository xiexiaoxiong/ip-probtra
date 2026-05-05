import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { ensureDatabaseReady } from '@/lib/db-init';
import { pgQuery } from '@/lib/postgres';

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  await ensureDatabaseReady();
  const currentUser = await getCurrentUserFromRequest(request);
  if (!isAdmin(currentUser)) {
    return NextResponse.json({ error: '无权限访问管理员接口' }, { status: 403 });
  }

  const { id } = await params;
  const targetId = Number(id);
  if (!Number.isInteger(targetId) || targetId <= 0) {
    return NextResponse.json({ error: '用户 ID 无效' }, { status: 400 });
  }

  await pgQuery(
    `
      update users
      set status = 'rejected',
          approved_by = $2,
          updated_at = now()
      where id = $1
    `,
    [targetId, currentUser!.id],
  );

  return NextResponse.json({ success: true });
}
