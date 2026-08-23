import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync, updateResults, updateSessionStatus } from '@/lib/analysis-store';
import {
  invalidityPortalSessionStatus,
  invalidityReportPendingMessage,
} from '@/lib/invalidity-contracts';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  InvalidityServiceError,
  requestInvalidityService,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  if (!isAdmin(user) && session.userId !== user.id) {
    return NextResponse.json({ error: '无权查看该调查' }, { status: 403 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '');
  if (!investigationId) {
    return NextResponse.json(
      { error: '该会话缺少 invalidityInvestigationId，无法恢复调查状态' },
      { status: 409 },
    );
  }

  try {
    const [investigation, claimPayload] = await Promise.all([
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}`,
      ),
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/claims`,
      ),
    ]);
    const status = String(investigation.status || 'running');
    await updateSessionStatus(session.id, invalidityPortalSessionStatus(status));
    await updateResults(session.id, { invalidityStatus: status });
    let report: Record<string, unknown> | null = null;
    let reportError: string | null = null;
    try {
      const candidate = await requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/report-data`,
      );
      reportError = invalidityReportPendingMessage(candidate);
      report = reportError ? null : candidate;
    } catch (error) {
      if (error instanceof InvalidityServiceError && error.status === 404) {
        reportError = '报告快照尚未建立；以下仅显示已持久化的权利要求状态';
      } else {
        throw error;
      }
    }
    const refreshed = await getSessionAsync(session.id);
    return NextResponse.json({
      session: refreshed || session,
      investigation,
      report,
      report_error: reportError,
      claim_investigations: claimPayload.claims || [],
    });
  } catch (error) {
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '读取无效调查失败' }, { status: 500 });
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  if (!isAdmin(user) && session.userId !== user.id) {
    return NextResponse.json({ error: '无权停止该调查' }, { status: 403 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '');
  if (!investigationId) {
    return NextResponse.json(
      { error: '该会话缺少 invalidityInvestigationId，无法停止调查' },
      { status: 409 },
    );
  }

  try {
    const result = await requestInvalidityService<Record<string, unknown>>(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      `/v1/investigations/${encodeURIComponent(investigationId)}/cancel`,
      {
        method: 'POST',
        body: { reason: '用户在 Agent 页面请求强制停止本次分析' },
      },
    );
    const status = String(result.status || 'cancelled');
    await updateSessionStatus(session.id, invalidityPortalSessionStatus(status));
    await updateResults(session.id, { invalidityStatus: status });
    return NextResponse.json({
      session: await getSessionAsync(session.id),
      cancel: result,
    });
  } catch (error) {
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '停止无效调查失败' }, { status: 500 });
  }
}
