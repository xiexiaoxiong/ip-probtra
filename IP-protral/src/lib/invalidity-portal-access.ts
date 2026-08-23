import 'server-only';

import type { NextRequest } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import type { AnalysisSession, AuthUser } from '@/lib/types';

export class InvalidityPortalAccessError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'InvalidityPortalAccessError';
    this.status = status;
  }
}

export type InvalidityPortalSessionAccess = {
  user: AuthUser;
  session: AnalysisSession;
  investigationId: string;
  actor: string;
};

export function invalidityPortalActor(user: AuthUser): string {
  return `${user.role === 'admin' ? 'admin' : 'user'}:${user.id}`;
}

export async function requireInvaliditySession(
  request: NextRequest,
  sessionId: string,
): Promise<InvalidityPortalSessionAccess> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) throw new InvalidityPortalAccessError('请先登录', 401);
  if (user.status !== 'approved') {
    throw new InvalidityPortalAccessError('账号尚未获准开展分析', 403);
  }
  const session = await getSessionAsync(sessionId);
  if (!session || session.analysisKind !== 'invalidity') {
    throw new InvalidityPortalAccessError('无效调查会话不存在', 404);
  }
  if (!isAdmin(user) && session.userId !== user.id) {
    throw new InvalidityPortalAccessError('无权访问该调查', 403);
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '').trim();
  if (!investigationId) {
    throw new InvalidityPortalAccessError('调查尚未建立', 409);
  }
  return {
    user,
    session,
    investigationId,
    actor: invalidityPortalActor(user),
  };
}

// Transitional alias for extensions compiled against the former split-stack name.
export const requireProdInvaliditySession = requireInvaliditySession;

export function clientReviewBody(input: unknown): Record<string, unknown> {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new InvalidityPortalAccessError('人工复核请求必须是 JSON 对象', 400);
  }
  const body = input as Record<string, unknown>;
  const forbidden = [
    'actor',
    'actor_id',
    'environment',
    'source_path',
    'backend_path',
    'investigation_id',
    'expected_source_sha256',
    'expected_source_byte_size',
    'expected_source_mime_type',
    'upload_receipt',
  ].filter((key) => body[key] !== undefined);
  if (forbidden.length) {
    throw new InvalidityPortalAccessError(
      `浏览器不得提交服务端控制字段：${forbidden.join('、')}`,
      400,
    );
  }
  return body;
}
