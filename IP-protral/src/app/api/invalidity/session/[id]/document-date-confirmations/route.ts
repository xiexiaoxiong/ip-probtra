import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse } from '@/lib/auth';
import {
  assertHumanReviewCommandResponse,
  isJsonObject,
  type DocumentDateConfirmationRequest,
} from '@/lib/invalidity-contracts';
import {
  clientReviewBody,
  InvalidityPortalAccessError,
  requireInvaliditySession,
} from '@/lib/invalidity-portal-access';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  confirmInvalidityDocumentDate,
  InvalidityServiceError,
  invalidityServiceErrorCode,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function required(value: unknown, field: string): string {
  const result = text(value);
  if (!result) throw new InvalidityPortalAccessError(`${field} 不能为空`, 400);
  return result;
}

function date(value: unknown, field: string): string | undefined {
  const result = text(value);
  if (!result) return undefined;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(result);
  const parsed = match
    ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
    : null;
  if (
    !match
    || !parsed
    || parsed.getUTCFullYear() !== Number(match[1])
    || parsed.getUTCMonth() !== Number(match[2]) - 1
    || parsed.getUTCDate() !== Number(match[3])
  ) {
    throw new InvalidityPortalAccessError(`${field} 必须是 YYYY-MM-DD`, 400);
  }
  return result;
}

function selectedClaimIds(value: unknown): string[] {
  if (!Array.isArray(value)) throw new InvalidityPortalAccessError('必须选择目标权利要求', 400);
  const ids = [...new Set(value.map((item) => String(item).trim()).filter(Boolean))];
  if (!ids.length) throw new InvalidityPortalAccessError('必须选择目标权利要求', 400);
  return ids;
}

function claimStateVersions(value: unknown, ids: string[]): Record<string, number> {
  if (!isJsonObject(value)) throw new InvalidityPortalAccessError('缺少权利要求状态版本', 400);
  return Object.fromEntries(ids.map((id) => {
    const version = Number(value[id]);
    if (!Number.isInteger(version) || version < 0) {
      throw new InvalidityPortalAccessError(`权利要求 ${id} 的状态版本无效`, 400);
    }
    return [id, version];
  }));
}

function qualifications(value: unknown, ids: string[]) {
  if (!isJsonObject(value)) throw new InvalidityPortalAccessError('缺少上一资格版本', 400);
  return Object.fromEntries(ids.map((id) => {
    const item = value[id];
    if (!isJsonObject(item)) throw new InvalidityPortalAccessError(`权利要求 ${id} 缺少上一资格`, 400);
    const qualificationId = required(item.qualification_id, `权利要求 ${id} qualification_id`);
    const assessmentVersion = Number(item.assessment_version);
    if (!Number.isInteger(assessmentVersion) || assessmentVersion < 1) {
      throw new InvalidityPortalAccessError(`权利要求 ${id} assessment_version 无效`, 400);
    }
    return [id, { qualification_id: qualificationId, assessment_version: assessmentVersion }];
  }));
}

function evidence(value: unknown): DocumentDateConfirmationRequest['date_evidence'] {
  if (!isJsonObject(value)) throw new InvalidityPortalAccessError('必须填写逐项日期证据', 400);
  const result: DocumentDateConfirmationRequest['date_evidence'] = {};
  for (const field of ['public_availability_date', 'publication_date', 'filing_date', 'priority_date'] as const) {
    const item = value[field];
    if (item == null) continue;
    if (!isJsonObject(item)) throw new InvalidityPortalAccessError(`${field} 日期证据无效`, 400);
    result[field] = {
      source: required(item.source, `${field} 日期证据来源`),
      ...(text(item.locator) ? { locator: text(item.locator) } : {}),
      ...(text(item.artifact_id) ? { artifact_id: text(item.artifact_id) } : {}),
    };
  }
  return result;
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  try {
    const { id } = await params;
    const access = await requireInvaliditySession(request, id);
    const input = clientReviewBody(await request.json().catch(() => null));
    for (const forbidden of ['novelty_eligible', 'inventive_step_eligible', 'eligibility_type']) {
      if (input[forbidden] !== undefined) {
        throw new InvalidityPortalAccessError(`浏览器不得提交 ${forbidden}；资格由日期规则计算`, 400);
      }
    }
    const claimIds = selectedClaimIds(input.claim_investigation_ids);
    const investigationVersion = Number(input.expected_investigation_state_version);
    const reviewRevision = Number(input.expected_review_revision);
    if (!Number.isInteger(investigationVersion) || investigationVersion < 0) {
      throw new InvalidityPortalAccessError('调查状态版本无效，请刷新后重试', 400);
    }
    if (!Number.isInteger(reviewRevision) || reviewRevision < 0) {
      throw new InvalidityPortalAccessError('人工复核版本无效，请刷新后重试', 400);
    }
    const decision = required(input.decision, '日期确认决定');
    if (!['confirm_facts', 'exclude', 'reopen_review'].includes(decision)) {
      throw new InvalidityPortalAccessError('日期确认决定无效', 400);
    }
    const channel = required(input.date_channel, '日期通道');
    if (!['ordinary_prior_art', 'cn_conflicting_application'].includes(channel)) {
      throw new InvalidityPortalAccessError('日期通道无效', 400);
    }
    const idempotencyKey = required(input.idempotency_key, 'idempotency_key');
    if (idempotencyKey.length < 8) throw new InvalidityPortalAccessError('idempotency_key 至少需要 8 个字符', 400);
    const dateFacts = {
      public_availability_date: date(input.public_availability_date, '公众可得日'),
      publication_date: date(input.publication_date, '公开日'),
      filing_date: date(input.filing_date, '申请日'),
      priority_date: date(input.priority_date, '优先权日'),
    };
    const providedDateFacts = Object.entries(dateFacts).filter((entry): entry is [string, string] => Boolean(entry[1]));
    if (decision === 'confirm_facts' && !providedDateFacts.length) {
      throw new InvalidityPortalAccessError('确认日期事实时至少填写一个真实日期', 400);
    }
    const dateEvidence = evidence(input.date_evidence);
    for (const [field] of providedDateFacts) {
      if (!dateEvidence[field as keyof typeof dateEvidence]) {
        throw new InvalidityPortalAccessError(`${field} 缺少逐项日期证据`, 400);
      }
    }
    const body: DocumentDateConfirmationRequest = {
      contract_version: 'v1',
      expected_investigation_state_version: investigationVersion,
      expected_claim_state_versions: claimStateVersions(input.expected_claim_state_versions, claimIds),
      expected_review_revision: reviewRevision,
      idempotency_key: idempotencyKey,
      reason: required(input.reason, '人工确认理由'),
      decision: decision as DocumentDateConfirmationRequest['decision'],
      document_id: required(input.document_id, 'document_id'),
      document_version_id: required(input.document_version_id, 'document_version_id'),
      claim_investigation_ids: claimIds,
      expected_qualifications: qualifications(input.expected_qualifications, claimIds),
      ...(dateFacts.public_availability_date ? { public_availability_date: dateFacts.public_availability_date } : {}),
      ...(dateFacts.publication_date ? { publication_date: dateFacts.publication_date } : {}),
      ...(dateFacts.filing_date ? { filing_date: dateFacts.filing_date } : {}),
      ...(dateFacts.priority_date ? { priority_date: dateFacts.priority_date } : {}),
      source_type: required(input.source_type, '日期证据来源类型'),
      ...(text(input.publication_number) ? { publication_number: text(input.publication_number) } : {}),
      ...(text(input.authority) ? { authority: text(input.authority) } : {}),
      ...(text(input.cn_application_scope) ? { cn_application_scope: text(input.cn_application_scope) } : {}),
      date_channel: channel as DocumentDateConfirmationRequest['date_channel'],
      date_evidence: dateEvidence,
    };
    const response = await confirmInvalidityDocumentDate(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      access.investigationId,
      body,
      access.actor,
    );
    assertHumanReviewCommandResponse(response);
    return NextResponse.json(response, { status: 202, headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof InvalidityPortalAccessError) {
      if (error.status === 401) return createUnauthorizedResponse(request);
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityServiceError) {
      if ([404, 405, 501].includes(error.status)) {
        return NextResponse.json(
          { code: 'REVIEW_ACTION_PENDING', error: '候选文献日期确认后端契约尚未启用，本次未保存决定' },
          { status: 501 },
        );
      }
      const code = invalidityServiceErrorCode(error);
      return NextResponse.json(
        { error: error.message, ...(code ? { code } : {}) },
        { status: error.status },
      );
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '保存候选文献日期确认失败' },
      { status: 500 },
    );
  }
}
