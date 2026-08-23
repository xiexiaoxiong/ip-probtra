import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse } from '@/lib/auth';
import {
  assertHumanReviewCommandResponse,
  humanReviewUploadReceiptMatches,
  isJsonObject,
  type EvidenceImportRequest,
  type JsonObject,
} from '@/lib/invalidity-contracts';
import {
  clientReviewBody,
  InvalidityPortalAccessError,
  requireInvaliditySession,
} from '@/lib/invalidity-portal-access';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  importInvalidityEvidence,
  InvalidityServiceError,
  invalidityServiceErrorCode,
} from '@/lib/invalidity-service';
import {
  claimInvalidityUploadReceipt,
  consumeInvalidityUploadReceipt,
  InvalidityUploadReceiptError,
} from '@/lib/invalidity-upload-receipts';
import { InvalidityUploadStorageError } from '@/lib/invalidity-upload-storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

function requiredText(value: unknown, field: string): string {
  const result = typeof value === 'string' ? value.trim() : '';
  if (!result) throw new InvalidityPortalAccessError(`${field} 不能为空`, 400);
  return result;
}

function optionalText(value: unknown): string | undefined {
  const result = typeof value === 'string' ? value.trim() : '';
  return result || undefined;
}

function optionalDate(value: unknown, field: string): string | undefined {
  const result = optionalText(value);
  if (!result) return undefined;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(result);
  const parsed = match
    ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
    : null;
  if (
    !match
    || parsed?.getUTCFullYear() !== Number(match[1])
    || parsed.getUTCMonth() !== Number(match[2]) - 1
    || parsed.getUTCDate() !== Number(match[3])
  ) {
    throw new InvalidityPortalAccessError(`${field} 必须是 YYYY-MM-DD`, 400);
  }
  return result;
}

function claimIds(value: unknown): string[] {
  if (!Array.isArray(value)) throw new InvalidityPortalAccessError('必须选择目标权利要求', 400);
  const result = [...new Set(value.map((item) => String(item).trim()).filter(Boolean))];
  if (!result.length) throw new InvalidityPortalAccessError('必须选择目标权利要求', 400);
  return result;
}

function expectedClaimVersions(value: unknown, ids: string[]): Record<string, number> {
  if (!isJsonObject(value)) {
    throw new InvalidityPortalAccessError('缺少权利要求状态版本，请刷新后重试', 400);
  }
  return Object.fromEntries(ids.map((id) => {
    const version = Number(value[id]);
    if (!Number.isInteger(version) || version < 0) {
      throw new InvalidityPortalAccessError(`权利要求 ${id} 的状态版本无效`, 400);
    }
    return [id, version];
  }));
}

function evidenceReference(value: unknown, field: string): JsonObject | undefined {
  if (value == null) return undefined;
  if (!isJsonObject(value)) throw new InvalidityPortalAccessError(`${field} 日期证据无效`, 400);
  const source = requiredText(value.source, `${field} 日期证据来源`);
  return {
    source,
    ...(optionalText(value.locator) ? { locator: optionalText(value.locator) } : {}),
    ...(optionalText(value.artifact_id) ? { artifact_id: optionalText(value.artifact_id) } : {}),
  };
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  try {
    const { id } = await params;
    const access = await requireInvaliditySession(request, id);
    const input = clientReviewBody(await request.json().catch(() => null));
    if (input.source_url !== undefined) {
      throw new InvalidityPortalAccessError(
        'Portal 人工材料导入只接受 owner-bound upload_receipt_id',
        400,
      );
    }
    const selectedClaimIds = claimIds(input.claim_investigation_ids);
    const expectedInvestigationVersion = Number(input.expected_investigation_state_version);
    const expectedReviewRevision = Number(input.expected_review_revision);
    if (!Number.isInteger(expectedInvestigationVersion) || expectedInvestigationVersion < 0) {
      throw new InvalidityPortalAccessError('调查状态版本无效，请刷新后重试', 400);
    }
    if (!Number.isInteger(expectedReviewRevision) || expectedReviewRevision < 0) {
      throw new InvalidityPortalAccessError('人工复核版本无效，请刷新后重试', 400);
    }
    const idempotencyKey = requiredText(input.idempotency_key, 'idempotency_key');
    if (idempotencyKey.length < 8) {
      throw new InvalidityPortalAccessError('idempotency_key 至少需要 8 个字符', 400);
    }
    const receipt = await claimInvalidityUploadReceipt({
      receiptId: requiredText(input.upload_receipt_id, 'upload_receipt_id'),
      userId: access.user.id,
      sessionId: access.session.id,
      environment: CANONICAL_INVALIDITY_ENVIRONMENT,
      idempotencyKey,
    });
    const factsInput = isJsonObject(input.declared_date_facts) ? input.declared_date_facts : {};
    const dateChannel = optionalText(factsInput.date_channel);
    if (dateChannel && !['ordinary_prior_art', 'cn_conflicting_application'].includes(dateChannel)) {
      throw new InvalidityPortalAccessError('日期通道无效', 400);
    }
    const declaredDateFacts = {
      ...(optionalDate(factsInput.public_availability_date, '公众可得日') ? { public_availability_date: optionalDate(factsInput.public_availability_date, '公众可得日') } : {}),
      ...(optionalDate(factsInput.publication_date, '公开日') ? { publication_date: optionalDate(factsInput.publication_date, '公开日') } : {}),
      ...(optionalDate(factsInput.filing_date, '申请日') ? { filing_date: optionalDate(factsInput.filing_date, '申请日') } : {}),
      ...(optionalDate(factsInput.priority_date, '优先权日') ? { priority_date: optionalDate(factsInput.priority_date, '优先权日') } : {}),
      ...(dateChannel ? { date_channel: dateChannel as 'ordinary_prior_art' | 'cn_conflicting_application' } : {}),
    };
    const evidenceInput = isJsonObject(input.date_evidence) ? input.date_evidence : {};
    const dateEvidence = Object.fromEntries(
      ['public_availability_date', 'publication_date', 'filing_date', 'priority_date']
        .map((field) => [field, evidenceReference(evidenceInput[field], field)] as const)
        .filter((entry): entry is readonly [string, JsonObject] => Boolean(entry[1])),
    );
    const body: EvidenceImportRequest = {
      contract_version: 'v1',
      expected_investigation_state_version: expectedInvestigationVersion,
      expected_claim_state_versions: expectedClaimVersions(
        input.expected_claim_state_versions,
        selectedClaimIds,
      ),
      expected_review_revision: expectedReviewRevision,
      idempotency_key: idempotencyKey,
      reason: requiredText(input.reason, '人工导入理由'),
      claim_investigation_ids: selectedClaimIds,
      canonical_key: requiredText(input.canonical_key, '规范文献标识'),
      document_type: requiredText(input.document_type, '文献类型'),
      title: requiredText(input.title, '文献题名'),
      source_path: receipt.filePath,
      expected_source_sha256: receipt.sha256,
      expected_source_byte_size: receipt.byteSize,
      expected_source_mime_type: receipt.mimeType,
      ...(optionalText(input.language) ? { language: optionalText(input.language) } : {}),
      ...(optionalText(input.publication_number) ? { publication_number: optionalText(input.publication_number) } : {}),
      ...(optionalText(input.authority) ? { authority: optionalText(input.authority) } : {}),
      ...(Object.keys(declaredDateFacts).length ? { declared_date_facts: declaredDateFacts } : {}),
      ...(Object.keys(dateEvidence).length ? { date_evidence: dateEvidence } : {}),
    };
    const response = await importInvalidityEvidence(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      access.investigationId,
      body,
      access.actor,
    );
    assertHumanReviewCommandResponse(response);
    if (!humanReviewUploadReceiptMatches(response, receipt)) {
      throw new InvalidityServiceError('后端冻结文件与 owner-bound 上传收据不一致', 502, {
        code: 'UPLOAD_RECEIPT_MISMATCH',
      });
    }
    await consumeInvalidityUploadReceipt({
      receiptId: receipt.receiptId,
      userId: access.user.id,
      sessionId: access.session.id,
      environment: CANONICAL_INVALIDITY_ENVIRONMENT,
      idempotencyKeySha256: receipt.idempotencyKeySha256,
      upstreamActionId: response.review_action_id,
    });
    return NextResponse.json(response, { status: 202, headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof InvalidityPortalAccessError) {
      if (error.status === 401) return createUnauthorizedResponse(request);
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityUploadReceiptError || error instanceof InvalidityUploadStorageError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityServiceError) {
      if ([404, 405, 501].includes(error.status)) {
        return NextResponse.json(
          { code: 'REVIEW_ACTION_PENDING', error: '人工材料导入后端契约尚未启用，本次未创建复核动作' },
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
      { error: error instanceof Error ? error.message : '导入对比材料失败' },
      { status: 500 },
    );
  }
}
