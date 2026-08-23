import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync, updateResults, updateSessionStatus } from '@/lib/analysis-store';
import {
  isInvalidityClaimContinuable,
  isInvaliditySuccessfulClaimTerminal,
  reportRows,
  type InvestigationContinuationRequest,
} from '@/lib/invalidity-contracts';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  continueInvalidityInvestigation,
  InvalidityServiceError,
  requestInvalidityService,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function unavailableResponse(error: InvalidityServiceError): NextResponse {
  if ([404, 405, 501].includes(error.status)) {
    return NextResponse.json(
      { error: '人工继续后端契约尚未启用，本次没有创建新轮次' },
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
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  if (!isAdmin(user) && session.userId !== user.id) {
    return NextResponse.json({ error: '无权继续该调查' }, { status: 403 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '');
  if (!investigationId) return NextResponse.json({ error: '调查尚未建立' }, { status: 409 });

  const input = await request.json().catch(() => null) as Record<string, unknown> | null;
  const claimIds = Array.isArray(input?.claim_investigation_ids)
    ? [...new Set(input.claim_investigation_ids.map(String).map((item) => item.trim()).filter(Boolean))]
    : [];
  const rounds = Number(input?.max_additional_rounds);
  const reason = String(input?.reason || '').trim();
  const expectedStateVersion = Number(input?.expected_state_version);
  const idempotencyKey = String(input?.idempotency_key || '').trim();
  if (![1, 2, 3].includes(rounds)) {
    return NextResponse.json({ error: 'max_additional_rounds 必须为 1、2 或 3' }, { status: 400 });
  }
  if (!reason) return NextResponse.json({ error: '必须填写继续检索理由' }, { status: 400 });
  if (!Number.isInteger(expectedStateVersion) || expectedStateVersion < 0) {
    return NextResponse.json({ error: 'expected_state_version 无效，请刷新后重试' }, { status: 400 });
  }
  if (idempotencyKey.length < 8) {
    return NextResponse.json({ error: 'idempotency_key 至少需要 8 个字符' }, { status: 400 });
  }

  const body: InvestigationContinuationRequest = {
    contract_version: 'v1',
    ...(claimIds.length ? { claim_investigation_ids: claimIds } : {}),
    max_additional_rounds: rounds as 1 | 2 | 3,
    reason,
    expected_state_version: expectedStateVersion,
    idempotency_key: idempotencyKey,
  };
  try {
    if (claimIds.length) {
      const claimPayload = await requestInvalidityService<Record<string, unknown>>(
        CANONICAL_INVALIDITY_ENVIRONMENT,
        `/v1/investigations/${encodeURIComponent(investigationId)}/claims`,
      );
      const claimsById = new Map(
        reportRows(claimPayload.claims).map((claim) => [String(claim.id || ''), claim]),
      );
      const missingIds = claimIds.filter((claimId) => !claimsById.has(claimId));
      if (missingIds.length) {
        return NextResponse.json(
          { error: `所选权利要求不存在：${missingIds.join('、')}` },
          { status: 400 },
        );
      }
      const successfulIds = claimIds.filter((claimId) => (
        isInvaliditySuccessfulClaimTerminal(claimsById.get(claimId)?.status)
      ));
      if (successfulIds.length) {
        return NextResponse.json(
          { error: '新颖性或创造性证据已完整的权利要求不能普通续检；如需补强证据，请另建补充调查' },
          { status: 409 },
        );
      }
      const nonContinuableIds = claimIds.filter((claimId) => (
        !isInvalidityClaimContinuable(claimsById.get(claimId)?.status)
      ));
      if (nonContinuableIds.length) {
        return NextResponse.json(
          { error: `所选权利要求当前不可继续：${nonContinuableIds.join('、')}` },
          { status: 409 },
        );
      }
    }
    const continuation = await continueInvalidityInvestigation(CANONICAL_INVALIDITY_ENVIRONMENT, investigationId, body);
    const status = String(continuation.investigation_status || 'queued');
    await updateResults(session.id, { invalidityStatus: status });
    await updateSessionStatus(session.id, 'running');
    return NextResponse.json(continuation);
  } catch (error) {
    if (error instanceof InvalidityServiceError) return unavailableResponse(error);
    return NextResponse.json({ error: '创建人工继续轮次失败' }, { status: 500 });
  }
}
