import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  InvalidityServiceError,
  requestInvalidityServiceBinary,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type RouteContext = {
  params: Promise<{ id: string; index: string }>;
};

export async function GET(
  request: NextRequest,
  context: RouteContext,
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved' || !isAdmin(user)) {
    return NextResponse.json({ error: '仅管理员可以查看同任务诊断数据' }, { status: 403 });
  }

  const { id, index: rawIndex } = await context.params;
  const figureIndex = Number.parseInt(rawIndex, 10);
  if (!Number.isSafeInteger(figureIndex) || figureIndex < 0 || figureIndex > 999) {
    return NextResponse.json({ error: '目标专利附图不存在' }, { status: 404 });
  }

  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '').trim();
  if (!investigationId) {
    return NextResponse.json({ error: '该会话尚未建立调查任务' }, { status: 409 });
  }

  try {
    const upstream = await requestInvalidityServiceBinary(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      `/v1/investigations/${encodeURIComponent(investigationId)}/target-figures/${figureIndex}`,
    );
    const contentType = upstream.headers.get('content-type') || 'image/png';
    if (!['image/png', 'image/jpeg', 'image/tiff'].includes(contentType)) {
      return NextResponse.json({ error: '目标专利附图格式不支持' }, { status: 502 });
    }
    return new NextResponse(await upstream.arrayBuffer(), {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch (error) {
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json(
        { error: '目标专利附图读取失败' },
        { status: error.status >= 400 && error.status < 600 ? error.status : 502 },
      );
    }
    return NextResponse.json({ error: '目标专利附图读取失败' }, { status: 500 });
  }
}
