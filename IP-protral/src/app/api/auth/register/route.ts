import { NextRequest, NextResponse } from 'next/server';
import { ensureDatabaseReady } from '@/lib/db-init';
import { hashPassword } from '@/lib/password';
import { pgQuery } from '@/lib/postgres';

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export async function POST(request: NextRequest) {
  await ensureDatabaseReady();

  try {
    const body = (await request.json()) as {
      name?: string;
      email?: string;
      password?: string;
    };

    const name = body.name?.trim() || '';
    const email = body.email?.trim().toLowerCase() || '';
    const password = body.password || '';

    if (!name || !email || !password) {
      return NextResponse.json({ error: '姓名、邮箱和密码不能为空' }, { status: 400 });
    }

    if (!isValidEmail(email)) {
      return NextResponse.json({ error: '邮箱格式不正确' }, { status: 400 });
    }

    if (password.length < 8) {
      return NextResponse.json({ error: '密码长度至少为 8 位' }, { status: 400 });
    }

    const existing = await pgQuery<{ id: number }>('select id from users where email = $1 limit 1', [email]);
    if (existing.rowCount) {
      return NextResponse.json({ error: '该邮箱已注册' }, { status: 409 });
    }

    const passwordHash = await hashPassword(password);
    await pgQuery(
      `
        insert into users (name, email, password_hash, role, status)
        values ($1, $2, $3, 'user', 'pending')
      `,
      [name, email, passwordHash],
    );

    return NextResponse.json({
      success: true,
      message: '注册成功，等待管理员审批后即可登录',
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '注册失败' },
      { status: 500 },
    );
  }
}
