import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync, updateResults, updateSessionStatus } from '@/lib/analysis-store';
import {
  invalidityPortalSessionStatus,
  type CriticalDateConfirmationRequest,
  type CriticalDateDecision,
} from '@/lib/invalidity-contracts';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  confirmInvalidityCriticalDate,
  InvalidityServiceError,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const DECISIONS = new Set<CriticalDateDecision>([
  'confirm_priority',
  'use_filing_date',
  'set_manual_date',
]);

function validDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  return !Number.isNaN(new Date(`${value}T00:00:00Z`).getTime());
}

function unavailableResponse(error: InvalidityServiceError): NextResponse {
  if ([404, 405, 501].includes(error.status)) {
    return NextResponse.json(
      { error: '关键日确认后端契约尚未启用，确认未保存；请勿重新上传专利' },
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
    return NextResponse.json({ error: '无权确认该调查的关键日' }, { status: 403 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '');
  if (!investigationId) return NextResponse.json({ error: '调查尚未建立' }, { status: 409 });

  const input = await request.json().catch(() => null) as Record<string, unknown> | null;
  const claimInvestigationId = String(input?.claim_investigation_id || '').trim();
  const decision = String(input?.decision || '') as CriticalDateDecision;
  const confirmedDate = input?.confirmed_date;
  const targetPublicationDate = input?.target_publication_date;
  const basis = String(input?.basis || '').trim();
  const reason = String(input?.reason || '').trim();
  const expectedStateVersion = Number(input?.expected_state_version);
  const idempotencyKey = String(input?.idempotency_key || '').trim();
  if (!claimInvestigationId) {
    return NextResponse.json({ error: '缺少 claim_investigation_id' }, { status: 400 });
  }
  if (!DECISIONS.has(decision)) {
    return NextResponse.json({ error: '关键日确认 decision 无效' }, { status: 400 });
  }
  if (!validDate(confirmedDate) || !validDate(targetPublicationDate)) {
    return NextResponse.json({ error: '确认关键日和目标公开日必须是 YYYY-MM-DD' }, { status: 400 });
  }
  if (!basis || !reason) {
    return NextResponse.json({ error: '必须填写日期依据和确认理由' }, { status: 400 });
  }
  if (!Number.isInteger(expectedStateVersion) || expectedStateVersion < 0) {
    return NextResponse.json({ error: 'expected_state_version 无效，请刷新后重试' }, { status: 400 });
  }
  if (idempotencyKey.length < 8) {
    return NextResponse.json({ error: 'idempotency_key 至少需要 8 个字符' }, { status: 400 });
  }

  const body: CriticalDateConfirmationRequest = {
    contract_version: 'v1',
    claim_investigation_id: claimInvestigationId,
    decision,
    confirmed_date: confirmedDate,
    target_publication_date: targetPublicationDate,
    basis,
    reason,
    expected_state_version: expectedStateVersion,
    idempotency_key: idempotencyKey,
  };
  try {
    const confirmation = await confirmInvalidityCriticalDate(CANONICAL_INVALIDITY_ENVIRONMENT, investigationId, body);
    const status = String(confirmation.investigation_status || 'running');
    await updateResults(session.id, { invalidityStatus: status });
    await updateSessionStatus(session.id, invalidityPortalSessionStatus(status));
    return NextResponse.json(confirmation);
  } catch (error) {
    if (error instanceof InvalidityServiceError) return unavailableResponse(error);
    return NextResponse.json({ error: '保存关键日确认失败' }, { status: 500 });
  }
}
