import { NextRequest, NextResponse } from 'next/server';
import {
  createUnauthorizedResponse,
  getCurrentUserFromRequest,
} from '@/lib/auth';
import {
  InvalidityServiceError,
  requestInvalidityServiceBinary,
} from '@/lib/invalidity-service';
import {
  InvalidityTestResourceNotFoundError,
  invalidityTestResourceOwnership,
} from '@/lib/invalidity-test-resource-ownership';
import { isInvalidityTestResourceId } from '@/lib/invalidity-test-proxy-policy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type RouteContext = {
  params: Promise<{ moduleRunId: string; figureIndex: string }>;
};

export async function GET(
  request: NextRequest,
  context: RouteContext,
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 });
  }
  const { moduleRunId, figureIndex: rawFigureIndex } = await context.params;
  const figureIndex = Number.parseInt(rawFigureIndex, 10);
  if (
    !isInvalidityTestResourceId(moduleRunId)
    || !Number.isSafeInteger(figureIndex)
    || figureIndex < 0
    || figureIndex > 999
  ) {
    return NextResponse.json({ error: '目标专利附图不存在' }, { status: 404 });
  }
  try {
    await invalidityTestResourceOwnership().requireModuleRunWithInvestigation(
      moduleRunId,
      user.id,
    );
    const upstream = await requestInvalidityServiceBinary(
      'test',
      `/v1/lab/module-runs/${encodeURIComponent(moduleRunId)}/target-figures/${figureIndex}`,
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
    if (error instanceof InvalidityTestResourceNotFoundError) {
      return NextResponse.json({ error: '测试资源不存在' }, { status: 404 });
    }
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json(
        { error: '目标专利附图读取失败' },
        { status: error.status >= 400 && error.status < 600 ? error.status : 502 },
      );
    }
    return NextResponse.json({ error: '目标专利附图读取失败' }, { status: 500 });
  }
}
