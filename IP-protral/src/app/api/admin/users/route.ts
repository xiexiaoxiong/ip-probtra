import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { ensureDatabaseReady } from '@/lib/db-init';
import { pgQuery } from '@/lib/postgres';
import type { AuthUser } from '@/lib/types';

interface UserRow {
  id: number;
  name: string;
  email: string;
  role: AuthUser['role'];
  status: AuthUser['status'];
  approved_at: string | null;
  created_at: string | null;
  approved_by_name: string | null;
}

export async function GET(request: NextRequest) {
  await ensureDatabaseReady();
  const currentUser = await getCurrentUserFromRequest(request);
  if (!isAdmin(currentUser)) {
    return NextResponse.json({ error: '无权限访问管理员接口' }, { status: 403 });
  }

  const result = await pgQuery<UserRow>(
    `
      select
        u.id,
        u.name,
        u.email,
        u.role,
        u.status,
        u.approved_at,
        u.created_at,
        approver.name as approved_by_name
      from users u
      left join users approver on approver.id = u.approved_by
      order by
        case
          when u.status = 'pending' then 0
          when u.status = 'approved' then 1
          when u.status = 'rejected' then 2
          else 3
        end,
        u.created_at desc
    `,
  );

  return NextResponse.json({
    users: result.rows.map((row) => ({
      id: row.id,
      name: row.name,
      email: row.email,
      role: row.role,
      status: row.status,
      approvedAt: row.approved_at,
      createdAt: row.created_at,
      approvedByName: row.approved_by_name,
    })),
  });
}
