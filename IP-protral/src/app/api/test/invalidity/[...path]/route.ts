import { NextRequest, NextResponse } from 'next/server';
import {
  createUnauthorizedResponse,
  getCurrentUserFromRequest,
} from '@/lib/auth';
import {
  assertHumanReviewCommandResponse,
  assertInvalidityReviewContext,
  humanReviewUploadReceiptMatches,
  isJsonObject,
  liveLabPayloadViolation,
} from '@/lib/invalidity-contracts';
import {
  clientReviewBody,
  invalidityPortalActor,
} from '@/lib/invalidity-portal-access';
import {
  InvalidityServiceError,
  invalidityServiceErrorCode,
  requestInvalidityService,
  resolveInvalidityUploadPath,
} from '@/lib/invalidity-service';
import {
  invalidityTestProxyOperation,
  isInvalidityTestResourceId,
  scopedInvalidityTestIdentifier,
} from '@/lib/invalidity-test-proxy-policy';
import {
  invalidityTestResourceOwnership,
  InvalidityTestResourceNotFoundError,
} from '@/lib/invalidity-test-resource-ownership';
import {
  InvalidityUploadStorageError,
  prepareInvalidityUploadRoot,
} from '@/lib/invalidity-upload-storage';
import {
  claimInvalidityUploadReceipt,
  consumeInvalidityUploadReceipt,
  InvalidityUploadReceiptError,
  type ClaimedInvalidityUploadReceipt,
} from '@/lib/invalidity-upload-receipts';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type RouteContext = { params: Promise<{ path: string[] }> };
type ProxyMethod = 'GET' | 'POST';

class InvalidityTestUpstreamContractError extends Error {
  constructor() {
    super('测试服务返回了无效的资源契约');
    this.name = 'InvalidityTestUpstreamContractError';
  }
}

function pathname(parts: string[], request: NextRequest): string {
  const base = `/${parts.map(encodeURIComponent).join('/')}`;
  return `${base}${request.nextUrl.search}`;
}

function upstreamResourceId(data: unknown, field: string): string {
  const value = isJsonObject(data) ? data[field] : undefined;
  if (!isInvalidityTestResourceId(value)) {
    throw new InvalidityTestUpstreamContractError();
  }
  return value;
}

function resourceNotFoundResponse(): NextResponse {
  return NextResponse.json({ error: '测试资源不存在' }, { status: 404 });
}

async function proxy(
  request: NextRequest,
  context: RouteContext,
  method: ProxyMethod,
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 });
  }

  try {
    const { path } = await context.params;
    const operation = invalidityTestProxyOperation(method, path);
    if (!operation) {
      return NextResponse.json({ error: '测试代理路径不存在' }, { status: 404 });
    }

    const ownership = invalidityTestResourceOwnership();
    let body: unknown = method === 'GET'
      ? undefined
      : await request.json().catch(() => ({}));
    let associatedInvestigationId: string | null = null;
    let controlledModuleInvestigationId: string | null = null;
    let claimedEvidenceReceipt: ClaimedInvalidityUploadReceipt | null = null;

    if (operation.kind === 'create_investigation') {
      await prepareInvalidityUploadRoot('test', { create: true });
      if (!isJsonObject(body)) {
        return NextResponse.json({ error: '创建测试调查必须提供 JSON 对象' }, { status: 400 });
      }
      if (body.environment !== undefined && body.environment !== 'test') {
        return NextResponse.json({ error: 'Portal 测试接口只接受 environment=test' }, { status: 400 });
      }
      if (body.source_path !== undefined && body.source_path !== null) {
        return NextResponse.json({ error: 'Portal 测试接口不接受客户端 source_path，只接受上传 fileKey' }, { status: 400 });
      }
      const sourceFileKey = String(body.source_file_key || '').trim();
      const safeBody: Record<string, unknown> = { ...body };
      delete safeBody.source_file_key;
      delete safeBody.environment;
      try {
        safeBody.analysis_session_id = scopedInvalidityTestIdentifier(
          'investigation-session',
          user.id,
          body.analysis_session_id,
        );
        safeBody.idempotency_key = scopedInvalidityTestIdentifier(
          'investigation-key',
          user.id,
          body.idempotency_key,
        );
      } catch {
        return NextResponse.json(
          { error: '创建测试调查必须提供 analysis_session_id 和 idempotency_key' },
          { status: 400 },
        );
      }
      if (sourceFileKey) {
        body = {
          ...safeBody,
          source_path: await resolveInvalidityUploadPath('test', sourceFileKey),
        };
      } else {
        body = safeBody;
      }
    }

    if (
      operation.kind === 'start_investigation'
      || operation.kind === 'read_investigation'
      || operation.kind === 'read_report_data'
      || operation.kind === 'read_review_context'
      || operation.kind === 'import_evidence'
      || operation.kind === 'confirm_document_date'
    ) {
      await ownership.require('investigation', operation.investigationId, user.id);
    }

    if (operation.kind === 'import_evidence' || operation.kind === 'confirm_document_date') {
      const reviewBody = clientReviewBody(body);
      if (operation.kind === 'confirm_document_date') {
        for (const forbidden of ['novelty_eligible', 'inventive_step_eligible', 'eligibility_type']) {
          if (reviewBody[forbidden] !== undefined) {
            return NextResponse.json(
              { error: `浏览器不得提交 ${forbidden}；资格由日期规则计算` },
              { status: 400 },
            );
          }
        }
      }
      if (operation.kind === 'import_evidence') {
        if (reviewBody.source_url !== undefined) {
          return NextResponse.json(
            { error: 'Portal 测试材料导入只接受 owner-bound upload_receipt_id' },
            { status: 400 },
          );
        }
        const idempotencyKey = String(reviewBody.idempotency_key || '').trim();
        claimedEvidenceReceipt = await claimInvalidityUploadReceipt({
          receiptId: String(reviewBody.upload_receipt_id || '').trim(),
          userId: user.id,
          sessionId: `test-investigation:${operation.investigationId}`,
          environment: 'test',
          idempotencyKey,
        });
        const safeBody = { ...reviewBody };
        delete safeBody.upload_receipt_id;
        body = {
          ...safeBody,
          source_path: claimedEvidenceReceipt.filePath,
          expected_source_sha256: claimedEvidenceReceipt.sha256,
          expected_source_byte_size: claimedEvidenceReceipt.byteSize,
          expected_source_mime_type: claimedEvidenceReceipt.mimeType,
        };
      } else {
        body = reviewBody;
      }
    }

    if (operation.kind === 'create_module_run') {
      if (!isJsonObject(body)) {
        return NextResponse.json({ error: '创建模块运行必须提供 JSON 对象' }, { status: 400 });
      }
      associatedInvestigationId = String(body.investigation_id || '').trim() || null;
      const claimInvestigationId = String(body.claim_investigation_id || '').trim();
      if (claimInvestigationId && !associatedInvestigationId) {
        return NextResponse.json(
          { error: '设置 claim_investigation_id 时必须提供 investigation_id' },
          { status: 400 },
        );
      }
      if (associatedInvestigationId) {
        await ownership.require('investigation', associatedInvestigationId, user.id);
      }
      if (body.input_mode === 'live') {
        const violation = liveLabPayloadViolation(body.input);
        if (violation) {
          return NextResponse.json({ error: `live 输入被拒绝：${violation}` }, { status: 400 });
        }
      }
      try {
        body = {
          ...body,
          idempotency_key: scopedInvalidityTestIdentifier(
            'module-key',
            user.id,
            body.idempotency_key,
          ),
        };
      } catch {
        return NextResponse.json(
          { error: '创建模块运行必须提供 idempotency_key' },
          { status: 400 },
        );
      }
    }

    if (
      operation.kind === 'read_module_run'
      || operation.kind === 'cancel_module_run'
      || operation.kind === 'retry_module_run'
    ) {
      const owner = await ownership.requireModuleRunWithInvestigation(
        operation.moduleRunId,
        user.id,
      );
      controlledModuleInvestigationId = owner.investigationId;
    }

    if (operation.kind === 'cancel_module_run') {
      if (!isJsonObject(body) || !String(body.reason || '').trim()) {
        return NextResponse.json({ error: '取消模块运行必须填写原因' }, { status: 400 });
      }
      body = {
        contract_version: 'v1',
        reason: String(body.reason).trim(),
      };
    }

    if (operation.kind === 'retry_module_run') {
      if (
        !isJsonObject(body)
        || !String(body.reason || '').trim()
        || !String(body.idempotency_key || '').trim()
      ) {
        return NextResponse.json(
          { error: '人工重试必须填写原因和 idempotency_key' },
          { status: 400 },
        );
      }
      body = {
        contract_version: 'v1',
        reason: String(body.reason).trim(),
        idempotency_key: scopedInvalidityTestIdentifier(
          'module-retry-key',
          user.id,
          body.idempotency_key,
        ),
      };
    }

    const data = await requestInvalidityService<Record<string, unknown>>(
      'test',
      pathname(path, request),
      {
        method,
        body,
        timeoutMs: ['create_investigation', 'import_evidence'].includes(operation.kind)
          ? 180_000
          : 60_000,
        ...(
          operation.kind === 'import_evidence' || operation.kind === 'confirm_document_date'
            ? { headers: { 'X-Invalidity-Actor': invalidityPortalActor(user) } }
            : {}
        ),
      },
    );

    if (operation.kind === 'read_review_context') {
      assertInvalidityReviewContext(data);
      if (data.investigation_id !== operation.investigationId) {
        throw new InvalidityTestUpstreamContractError();
      }
    }

    if (operation.kind === 'import_evidence' || operation.kind === 'confirm_document_date') {
      assertHumanReviewCommandResponse(data);
    }

    if (operation.kind === 'import_evidence' && claimedEvidenceReceipt) {
      if (!humanReviewUploadReceiptMatches(data, claimedEvidenceReceipt)) {
        throw new InvalidityServiceError('测试后端冻结文件与 owner-bound 上传收据不一致', 502, {
          code: 'UPLOAD_RECEIPT_MISMATCH',
        });
      }
      await consumeInvalidityUploadReceipt({
        receiptId: claimedEvidenceReceipt.receiptId,
        userId: user.id,
        sessionId: `test-investigation:${operation.investigationId}`,
        environment: 'test',
        idempotencyKeySha256: claimedEvidenceReceipt.idempotencyKeySha256,
        upstreamActionId: String(data.review_action_id || ''),
      });
    }

    if (operation.kind === 'create_investigation') {
      const investigationId = upstreamResourceId(data, 'investigation_id');
      await ownership.bind({
        resourceKind: 'investigation',
        resourceId: investigationId,
        userId: user.id,
        investigationId: null,
      });
    }

    if (operation.kind === 'start_investigation') {
      const returnedInvestigationId = upstreamResourceId(data, 'investigation_id');
      if (returnedInvestigationId !== operation.investigationId) {
        throw new InvalidityTestUpstreamContractError();
      }
      const moduleRunId = upstreamResourceId(data, 'module_run_id');
      await ownership.bind({
        resourceKind: 'module_run',
        resourceId: moduleRunId,
        userId: user.id,
        investigationId: operation.investigationId,
      });
    }

    if (operation.kind === 'create_module_run') {
      const investigationId = upstreamResourceId(data, 'investigation_id');
      const moduleRunId = upstreamResourceId(data, 'module_run_id');
      if (associatedInvestigationId && associatedInvestigationId !== investigationId) {
        throw new InvalidityTestUpstreamContractError();
      }
      if (!associatedInvestigationId) {
        await ownership.bind({
          resourceKind: 'investigation',
          resourceId: investigationId,
          userId: user.id,
          investigationId: null,
        });
      }
      await ownership.bind({
        resourceKind: 'module_run',
        resourceId: moduleRunId,
        userId: user.id,
        investigationId,
      });
    }

    if (operation.kind === 'cancel_module_run') {
      const moduleRunId = upstreamResourceId(data, 'module_run_id');
      const investigationId = upstreamResourceId(data, 'investigation_id');
      if (
        moduleRunId !== operation.moduleRunId
        || investigationId !== controlledModuleInvestigationId
      ) {
        throw new InvalidityTestUpstreamContractError();
      }
    }

    if (operation.kind === 'retry_module_run') {
      const retriedModuleRunId = upstreamResourceId(data, 'module_run_id');
      const retryOfModuleRunId = upstreamResourceId(data, 'retry_of_module_run_id');
      const investigationId = upstreamResourceId(data, 'investigation_id');
      if (
        retryOfModuleRunId !== operation.moduleRunId
        || retriedModuleRunId === operation.moduleRunId
        || investigationId !== controlledModuleInvestigationId
      ) {
        throw new InvalidityTestUpstreamContractError();
      }
      await ownership.bind({
        resourceKind: 'module_run',
        resourceId: retriedModuleRunId,
        userId: user.id,
        investigationId,
      });
    }

    if (operation.kind === 'read_module_run' && isJsonObject(data)) {
      const snapshot = isJsonObject(data.input_snapshot) ? data.input_snapshot : {};
      if (snapshot.input_mode === 'live') {
        const violation = liveLabPayloadViolation(data, 'response');
        if (violation) {
          return NextResponse.json(
            { error: `live 运行返回 fixture/manual 标记，测试代理已拒绝转发：${violation}` },
            { status: 502 },
          );
        }
      }
    }
    const queuedReview = operation.kind === 'import_evidence' || operation.kind === 'confirm_document_date';
    return NextResponse.json(data, {
      status: queuedReview ? 202 : 200,
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof InvalidityTestResourceNotFoundError) {
      return resourceNotFoundResponse();
    }
    if (error instanceof InvalidityTestUpstreamContractError) {
      return NextResponse.json({ error: error.message }, { status: 502 });
    }
    if (error instanceof InvalidityServiceError) {
      const code = invalidityServiceErrorCode(error);
      return NextResponse.json(
        { error: error.message, ...(code ? { code } : {}) },
        { status: error.status },
      );
    }
    if (error instanceof InvalidityUploadStorageError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityUploadReceiptError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '无效检索测试代理失败' }, { status: 500 });
  }
}

export function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context, 'GET');
}

export function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context, 'POST');
}
