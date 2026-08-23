import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse } from '@/lib/auth';
import { assertInvalidityReviewContext } from '@/lib/invalidity-contracts';
import {
  InvalidityPortalAccessError,
  requireInvaliditySession,
} from '@/lib/invalidity-portal-access';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  getInvalidityReviewContext,
  InvalidityServiceError,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  try {
    const { id } = await params;
    const access = await requireInvaliditySession(request, id);
    const context = await getInvalidityReviewContext(CANONICAL_INVALIDITY_ENVIRONMENT, access.investigationId);
    assertInvalidityReviewContext(context);
    if (context.investigation_id !== access.investigationId) {
      return NextResponse.json({ error: '人工复核上下文资源不匹配' }, { status: 502 });
    }
    return NextResponse.json(context, { headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof InvalidityPortalAccessError) {
      if (error.status === 401) return createUnauthorizedResponse(request);
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityServiceError) {
      if ([404, 405, 501].includes(error.status)) {
        return NextResponse.json(
          {
            code: 'REVIEW_CONTEXT_PENDING',
            error: '人工复核后端契约尚未启用；当前页面只读，未提交任何决定',
          },
          { status: 503 },
        );
      }
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '读取人工复核上下文失败' },
      { status: 502 },
    );
  }
}
