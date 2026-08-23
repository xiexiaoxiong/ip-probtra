import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
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
  if (user.status !== 'approved' || !isAdmin(user)) {
    return NextResponse.json({ error: '仅管理员可以查看同任务诊断数据' }, { status: 403 });
  }

  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '').trim();
  if (!investigationId) {
    return NextResponse.json({ error: '该会话尚未建立调查任务' }, { status: 409 });
  }

  try {
    const [investigation, claimPayload, moduleRunPayload] = await Promise.all([
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}`,
      ),
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/claims`,
      ),
      // 同任务只读诊断需要各模块真实输入/输出快照（含模块1/2这类无独立 run 的
      // 阶段由报告预览回填，其余阶段以持久化 run 快照为准）。
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/module-runs`,
      ),
    ]);

    let report: Record<string, unknown> | null = null;
    let reportError: string | null = null;
    try {
      report = await requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/report-data?preview=true`,
      );
    } catch (error) {
      if (error instanceof InvalidityServiceError && [404, 409, 422].includes(error.status)) {
        reportError = error.message || '报告快照尚未建立';
      } else {
        throw error;
      }
    }

    return NextResponse.json({
      diagnostic_mode: 'same_task_read_only',
      session,
      investigation,
      report,
      report_error: reportError,
      claim_investigations: claimPayload.claims || [],
      module_run_details: moduleRunPayload.module_runs || [],
    });
  } catch (error) {
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '读取管理员诊断数据失败' }, { status: 500 });
  }
}
