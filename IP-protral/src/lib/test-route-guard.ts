import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';

export async function requireTestUser(request: NextRequest): Promise<NextResponse | null> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) {
    return createUnauthorizedResponse(request);
  }
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 });
  }
  return null;
}

export function requiredTestServiceUrl(
  variableName: string,
  forbiddenPorts: readonly number[],
): string {
  const raw = String(process.env[variableName] || '').trim();
  if (!raw) {
    throw new Error(`缺少测试服务配置 ${variableName}；禁止回落正式端口`);
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${variableName} 不是有效 URL`);
  }
  if (forbiddenPorts.includes(Number(parsed.port))) {
    throw new Error(`${variableName} 指向正式端口 ${parsed.port}，测试接口已拒绝调用`);
  }
  return raw.replace(/\/$/, '');
}

export function canonicalServiceUrl(
  variableName: string,
  aliases: readonly string[],
  defaultUrl: string,
): string {
  const raw = String(
    process.env[variableName]
      || aliases.map((name) => process.env[name]).find((value) => String(value || '').trim())
      || defaultUrl,
  ).trim();
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${variableName} 不是有效 URL`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error(`${variableName} 只允许 HTTP(S) URL`);
  return raw.replace(/\/$/, '');
}
