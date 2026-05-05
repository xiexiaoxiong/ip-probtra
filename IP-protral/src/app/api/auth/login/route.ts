import { NextRequest, NextResponse } from 'next/server';
import { createAuthSession, setAuthCookie } from '@/lib/auth';
import { ensureDatabaseReady } from '@/lib/db-init';
import { verifyPassword } from '@/lib/password';
import { pgQuery } from '@/lib/postgres';
import type { AuthUser } from '@/lib/types';

interface UserRow {
  id: number;
  name: string;
  email: string;
  password_hash: string;
  role: AuthUser['role'];
  status: AuthUser['status'];
  approved_at: string | null;
  created_at: string | null;
}

export async function POST(request: NextRequest) {
  await ensureDatabaseReady();

  try {
    const body = (await request.json()) as {
      email?: string;
      password?: string;
    };

    const email = body.email?.trim().toLowerCase() || '';
    const password = body.password || '';

    if (!email || !password) {
      return NextResponse.json({ error: '邮箱和密码不能为空' }, { status: 400 });
    }

    const result = await pgQuery<UserRow>(
      `
        select id, name, email, password_hash, role, status, approved_at, created_at
        from users
        where email = $1
        limit 1
      `,
      [email],
    );

    const user = result.rows[0];
    if (!user) {
      return NextResponse.json({ error: '邮箱或密码错误' }, { status: 401 });
    }

    const passwordValid = await verifyPassword(password, user.password_hash);
    if (!passwordValid) {
      return NextResponse.json({ error: '邮箱或密码错误' }, { status: 401 });
    }

    if (user.status === 'pending') {
      return NextResponse.json({ error: '账号待管理员审批后才能登录' }, { status: 403 });
    }
    if (user.status === 'rejected') {
      return NextResponse.json({ error: '账号申请已被拒绝' }, { status: 403 });
    }
    if (user.status === 'disabled') {
      return NextResponse.json({ error: '账号已被禁用' }, { status: 403 });
    }

    const { token, expiresAt } = await createAuthSession(user.id);
    const response = NextResponse.json({
      success: true,
      user: {
        id: user.id,
        name: user.name,
        email: user.email,
        role: user.role,
        status: user.status,
        approvedAt: user.approved_at,
        createdAt: user.created_at,
      },
    });
    setAuthCookie(response, token, expiresAt);
    return response;
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '登录失败' },
      { status: 500 },
    );
  }
}
