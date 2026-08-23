import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import { getSessionAsync, updateResults, updateSessionStatus } from '@/lib/analysis-store';
import { AGENT_MAX_GAP_SEARCH_ROUNDS } from '@/lib/agent-invalidity-action';
import { reportRows, type InvestigationContinuationRequest } from '@/lib/invalidity-contracts';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  continueInvalidityInvestigation,
  InvalidityServiceError,
  requestInvalidityService,
} from '@/lib/invalidity-service';
import { PATENT_AGENT_TOOLSET_VERSION } from '@/lib/patent-agent';
import { getPatentAgentToolRun } from '@/lib/patent-agent-store';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function integer(value: unknown): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : -1;
}

function unavailableResponse(error: InvalidityServiceError): NextResponse {
  if ([404, 405, 501].includes(error.status)) {
    return NextResponse.json(
      { error: '续检后端契约尚未启用，本次没有创建新轮次' },
      { status: 501 },
    );
  }
  return NextResponse.json({ error: error.message }, { status: error.status });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  const { id } = await params;
  const tool = await getPatentAgentToolRun(id, user);
  if (!tool) return NextResponse.json({ error: 'Agent 工具任务不存在' }, { status: 404 });
  if (tool.toolKind !== 'invalidity' || tool.toolVersion !== PATENT_AGENT_TOOLSET_VERSION) {
    return NextResponse.json({ error: '该任务不支持 Agent 自动续检' }, { status: 409 });
  }
  if (!tool.analysisSessionId || !tool.investigationId) {
    return NextResponse.json({ error: 'Agent 工具任务尚未绑定无效调查' }, { status: 409 });
  }
  const session = await getSessionAsync(tool.analysisSessionId);
  if (!session || session.userId !== user.id || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在或无权访问' }, { status: 404 });
  }
  const input = await request.json().catch(() => null) as Record<string, unknown> | null;
  const expectedStateVersion = integer(input?.expected_state_version);
  if (expectedStateVersion < 0) {
    return NextResponse.json({ error: 'expected_state_version 无效，请刷新后重试' }, { status: 400 });
  }

  try {
    const [investigation, claimPayload] = await Promise.all([
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(tool.investigationId)}`,
      ),
      requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(tool.investigationId)}/claims`,
      ),
    ]);
    const currentStateVersion = integer(investigation.state_version);
    const investigationStatus = String(investigation.status || '').toLowerCase();
    if (currentStateVersion !== expectedStateVersion) {
      if (currentStateVersion > expectedStateVersion) {
        return NextResponse.json({
          status: 'already_advanced',
          investigation_state_version: currentStateVersion,
        });
      }
      return NextResponse.json({ error: '调查状态版本已变化，请刷新后重试' }, { status: 409 });
    }
    if (investigationStatus !== 'partial') {
      return NextResponse.json(
        { error: '当前调查不处于可自动续检的部分完成检查点' },
        { status: 409 },
      );
    }
    const claimIds = reportRows(claimPayload.claims)
      .filter((claim) => {
        const currentIteration = integer(claim.current_iteration_no);
        return claim.in_scope !== false
          && String(claim.status || '').toLowerCase() === 'partial'
          && currentIteration >= 1
          && currentIteration <= AGENT_MAX_GAP_SEARCH_ROUNDS;
      })
      .map((claim) => String(claim.id || '').trim())
      .filter(Boolean)
      .sort();
    if (claimIds.length === 0) {
      return NextResponse.json(
        { error: '没有处于五轮上限内、可由 Agent 继续的独立权利要求' },
        { status: 409 },
      );
    }
    const body: InvestigationContinuationRequest = {
      contract_version: 'v1',
      claim_investigation_ids: claimIds,
      max_additional_rounds: 1,
      reason: 'Agent 检索调度检查点已获用户确认或 30 秒确认窗口届满，继续下一轮 gap 检索',
      expected_state_version: expectedStateVersion,
      idempotency_key: `agent-gap-${tool.id}-${expectedStateVersion}`,
    };
    const continuation = await continueInvalidityInvestigation(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      tool.investigationId,
      body,
    );
    const status = String(continuation.investigation_status || 'queued');
    await updateResults(session.id, { invalidityStatus: status });
    await updateSessionStatus(session.id, 'running');
    return NextResponse.json({ ...continuation, status: 'started' });
  } catch (error) {
    if (error instanceof InvalidityServiceError) return unavailableResponse(error);
    return NextResponse.json({ error: 'Agent 创建下一轮检索失败' }, { status: 500 });
  }
}
