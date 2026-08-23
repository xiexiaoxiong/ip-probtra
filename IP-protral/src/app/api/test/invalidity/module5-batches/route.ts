import { createHash, randomUUID } from 'crypto';
import { NextRequest, NextResponse } from 'next/server';
import type { QueryResultRow } from 'pg';
import {
  createUnauthorizedResponse,
  getCurrentUserFromRequest,
} from '@/lib/auth';
import { ensureDatabaseReady } from '@/lib/db-init';
import { isJsonObject } from '@/lib/invalidity-contracts';
import {
  InvalidityServiceError,
  requestInvalidityService,
} from '@/lib/invalidity-service';
import {
  isInvalidityTestResourceId,
  scopedInvalidityTestIdentifier,
} from '@/lib/invalidity-test-proxy-policy';
import { invalidityTestResourceOwnership } from '@/lib/invalidity-test-resource-ownership';
import { pgQuery } from '@/lib/postgres';
import type { AuthUser } from '@/lib/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type JsonObject = Record<string, unknown>;

interface BatchEntry {
  document_id: string;
  current_run_id: string | null;
  run_ids: string[];
  retry_count: number;
}

interface BatchRow extends QueryResultRow {
  id: string;
  user_id: number;
  investigation_id: string;
  claim_investigation_id: string;
  idempotency_key_sha256: string;
  manifest_sha256: string;
  document_ids: unknown;
  entries: unknown;
  closest_run_id: string | null;
  inventive_run_id: string | null;
  inventive_retry_count: number;
  status: string;
  last_error: string | null;
  created_at: string | Date;
  updated_at: string | Date;
  completed_at: string | Date | null;
}

interface RunDetail {
  module_run?: JsonObject;
  job?: JsonObject;
  events?: JsonObject[];
  connection_error?: string;
}

const TERMINAL_RUNS = new Set([
  'succeeded',
  'completed',
  'partial',
  'failed',
  'cancelled',
]);
const SUCCESS_RUNS = new Set(['succeeded', 'completed']);
const MAX_BATCH_DOCUMENTS = 50;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function sha256(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function canonicalHash(value: unknown): string {
  return sha256(JSON.stringify(value));
}

function documentIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(
    value
      .map((item) => text(item))
      .filter((item) => item && item.length <= 300),
  )];
}

function batchEntries(row: BatchRow): BatchEntry[] {
  const values = Array.isArray(row.entries) ? row.entries : [];
  const byDocument = new Map<string, BatchEntry>();
  values.forEach((value) => {
    if (!isJsonObject(value)) return;
    const documentId = text(value.document_id);
    if (!documentId) return;
    const runIds = Array.isArray(value.run_ids)
      ? value.run_ids.map(text).filter(isInvalidityTestResourceId)
      : [];
    const currentRunId = isInvalidityTestResourceId(value.current_run_id)
      ? value.current_run_id
      : runIds.at(-1) || null;
    byDocument.set(documentId, {
      document_id: documentId,
      current_run_id: currentRunId,
      run_ids: [...new Set(runIds)],
      retry_count: Math.max(0, Math.min(2, Number(value.retry_count) || 0)),
    });
  });
  return documentIds(row.document_ids).map(
    (documentId) => byDocument.get(documentId) || {
      document_id: documentId,
      current_run_id: null,
      run_ids: [],
      retry_count: 0,
    },
  );
}

function publicError(error: unknown): string {
  if (error instanceof InvalidityServiceError) return error.message;
  return error instanceof Error ? error.message : '测试服务连接中断';
}

async function loadBatch(
  userId: number,
  options: {
    batchId?: string;
    investigationId?: string;
    claimInvestigationId?: string;
    keyHash?: string;
  },
): Promise<BatchRow | null> {
  await ensureDatabaseReady();
  const result = options.batchId
    ? await pgQuery<BatchRow>(
        `
          select *
          from invalidity_test_module5_batches
          where id = $1 and user_id = $2
          limit 1
        `,
        [options.batchId, userId],
      )
    : options.keyHash
      ? await pgQuery<BatchRow>(
          `
            select *
            from invalidity_test_module5_batches
            where idempotency_key_sha256 = $1 and user_id = $2
            limit 1
          `,
          [options.keyHash, userId],
        )
    : await pgQuery<BatchRow>(
        `
          select *
          from invalidity_test_module5_batches
          where investigation_id = $1
            and claim_investigation_id = $2
            and user_id = $3
          order by created_at desc
          limit 1
        `,
        [options.investigationId, options.claimInvestigationId, userId],
      );
  return result.rows[0] || null;
}

async function saveBatch(
  row: BatchRow,
  values: {
    entries: BatchEntry[];
    closestRunId?: string | null;
    inventiveRunId?: string | null;
    inventiveRetryCount?: number;
    status: string;
    lastError?: string | null;
  },
): Promise<BatchRow> {
  const terminal = ['completed', 'partial', 'failed'].includes(values.status);
  const result = await pgQuery<BatchRow>(
    `
      update invalidity_test_module5_batches
      set entries = $2::jsonb,
          closest_run_id = $3,
          inventive_run_id = $4,
          inventive_retry_count = $5,
          status = $6,
          last_error = $7,
          completed_at = case
            when $8 then coalesce(completed_at, now())
            else null
          end,
          updated_at = now()
      where id = $1 and user_id = $9
      returning *
    `,
    [
      row.id,
      JSON.stringify(values.entries),
      values.closestRunId === undefined ? row.closest_run_id : values.closestRunId,
      values.inventiveRunId === undefined ? row.inventive_run_id : values.inventiveRunId,
      values.inventiveRetryCount === undefined
        ? row.inventive_retry_count
        : values.inventiveRetryCount,
      values.status,
      values.lastError === undefined ? row.last_error : values.lastError,
      terminal,
      row.user_id,
    ],
  );
  if (!result.rows[0]) throw new Error('模块六单篇比对批次不存在');
  return result.rows[0];
}

async function createModuleRun(
  row: BatchRow,
  userId: number,
  moduleCode: string,
  input: JsonObject,
  keySuffix: string,
): Promise<string> {
  const data = await requestInvalidityService<JsonObject>(
    'test',
    '/v1/lab/module-runs',
    {
      method: 'POST',
      body: {
        contract_version: 'v1',
        module_code: moduleCode,
        input_mode: 'live',
        investigation_id: row.investigation_id,
        claim_investigation_id: row.claim_investigation_id,
        input: { ...input, module6_batch_id: row.id },
        idempotency_key: scopedInvalidityTestIdentifier(
          'module-key',
          userId,
          `module6-batch:${row.id}:${keySuffix}`,
        ),
      },
    },
  );
  const runId = text(data.module_run_id);
  const investigationId = text(data.investigation_id);
  if (
    !isInvalidityTestResourceId(runId)
    || investigationId !== row.investigation_id
  ) {
    throw new Error('测试服务返回了无效的模块运行契约');
  }
  await invalidityTestResourceOwnership().bind({
    resourceKind: 'module_run',
    resourceId: runId,
    userId,
    investigationId: row.investigation_id,
  });
  return runId;
}

async function retryModuleRun(
  row: BatchRow,
  entry: BatchEntry,
  userId: number,
  retryNumber: number,
): Promise<string> {
  if (!entry.current_run_id) throw new Error('待补跑文献缺少原运行记录');
  const data = await requestInvalidityService<JsonObject>(
    'test',
    `/v1/lab/module-runs/${encodeURIComponent(entry.current_run_id)}/retries`,
    {
      method: 'POST',
      body: {
        contract_version: 'v1',
        reason: retryNumber === 1
          ? '模块六整批首遍完成后，补跑一次临时 GLM 或结构复核失败文献'
          : 'GLM 1210 载荷降级已部署，仅补跑该文献一次',
        idempotency_key: scopedInvalidityTestIdentifier(
          'module-retry-key',
          userId,
          `module6-batch:${row.id}:retry:${retryNumber}:${entry.document_id}`,
        ),
      },
    },
  );
  const runId = text(data.module_run_id);
  if (
    !isInvalidityTestResourceId(runId)
    || text(data.retry_of_module_run_id) !== entry.current_run_id
    || text(data.investigation_id) !== row.investigation_id
  ) {
    throw new Error('测试服务返回了无效的补跑契约');
  }
  await invalidityTestResourceOwnership().bind({
    resourceKind: 'module_run',
    resourceId: runId,
    userId,
    investigationId: row.investigation_id,
  });
  return runId;
}

async function readRun(runId: string): Promise<RunDetail> {
  try {
    return await requestInvalidityService<RunDetail>(
      'test',
      `/v1/lab/module-runs/${encodeURIComponent(runId)}`,
    );
  } catch (error) {
    return { connection_error: publicError(error) };
  }
}

function runStatus(detail: RunDetail): string {
  return text(detail.module_run?.status)
    || text(detail.job?.status)
    || (detail.connection_error ? 'connection_interrupted' : 'unknown');
}

function secondsBetween(start: unknown, end: unknown): number | undefined {
  const startMs = Date.parse(text(start));
  const endMs = Date.parse(text(end));
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return undefined;
  }
  return Math.round((endMs - startMs) / 100) / 10;
}

function runOutput(detail: RunDetail): JsonObject {
  const snapshot = detail.module_run?.output_snapshot;
  if (!isJsonObject(snapshot)) return {};
  return isJsonObject(snapshot.output) ? snapshot.output : snapshot;
}

function structuralReviewIncomplete(detail: RunDetail): boolean {
  return text(runOutput(detail).structural_review_status) === 'model_error';
}

function incompleteFeatureCount(detail: RunDetail): number {
  const disclosures = runOutput(detail).disclosures;
  if (!Array.isArray(disclosures)) return 0;
  return disclosures.filter((item) => (
    isJsonObject(item) && text(item.status) === 'analysis_failed'
  )).length;
}

function comparisonHasUsableRows(detail: RunDetail): boolean {
  const disclosures = runOutput(detail).disclosures;
  return SUCCESS_RUNS.has(runStatus(detail))
    && Array.isArray(disclosures)
    && disclosures.some((item) => (
      isJsonObject(item) && text(item.status) !== 'analysis_failed'
    ));
}

function runSucceeded(detail: RunDetail): boolean {
  const disclosures = runOutput(detail).disclosures;
  return (
    SUCCESS_RUNS.has(runStatus(detail))
    && !structuralReviewIncomplete(detail)
    && (!Array.isArray(disclosures) || incompleteFeatureCount(detail) === 0)
  );
}

function runRetryable(detail: RunDetail): boolean {
  const code = text(detail.module_run?.error_code);
  const message = text(detail.module_run?.error_message);
  return structuralReviewIncomplete(detail)
    || detail.module_run?.retryable === true
    || code === '1210'
    || message.includes('error_code=1210');
}

function runRejectedWith1210(detail: RunDetail): boolean {
  const code = text(detail.module_run?.error_code);
  const message = text(detail.module_run?.error_message);
  const structuralReviewCode = text(
    runOutput(detail).structural_review_error_code,
  );
  return code === '1210'
    || structuralReviewCode === '1210'
    || message.includes('error_code=1210');
}

function childRun(
  code: string,
  detail: RunDetail,
  options: { documentId?: string; pipelineAttempts?: number } = {},
): JsonObject {
  const run = detail.module_run || {};
  const job = detail.job || {};
  const status = runStatus(detail);
  const isSingleReference = code === 'I4_S_SINGLE_REFERENCE';
  const usable = isSingleReference
    ? comparisonHasUsableRows(detail)
    : runSucceeded(detail);
  const missingFeatures = isSingleReference
    ? incompleteFeatureCount(detail)
    : 0;
  const createdAt = job.created_at || run.created_at;
  const startedAt = job.started_at || run.started_at;
  const finishedAt = job.finished_at || run.finished_at || run.completed_at;
  const now = new Date().toISOString();
  return {
    code,
    ok: usable,
    complete: runSucceeded(detail),
    status,
    output: runOutput(detail),
    ...(options.documentId ? { documentId: options.documentId } : {}),
    ...(options.pipelineAttempts
      ? { pipelineAttempts: options.pipelineAttempts }
      : {}),
    error: detail.connection_error
      || (structuralReviewIncomplete(detail)
        ? `整体结构复核未完成：${text(runOutput(detail).structural_review_error_code) || 'MODEL_ERROR'}`
        : undefined)
      || (missingFeatures > 0
        ? `${missingFeatures} 项技术特征分析未完成；已完成的逐项结果仍保留展示，但不能进入整体聚合`
        : undefined)
      || text(run.error_message)
      || text(run.error_code)
      || undefined,
    attempts: Number(job.attempt_count) || undefined,
    queueSeconds: secondsBetween(createdAt, startedAt || now),
    executionSeconds: startedAt
      ? secondsBetween(startedAt, finishedAt || now)
      : undefined,
    moduleRunId: text(run.id) || undefined,
    raw: detail,
  };
}

async function advanceBatch(row: BatchRow): Promise<JsonObject> {
  const entries = batchEntries(row);
  let lastError: string | null = null;

  for (const entry of entries) {
    if (entry.current_run_id) continue;
    try {
      const runId = await createModuleRun(
        row,
        row.user_id,
        'I4_S_SINGLE_REFERENCE',
        { document_id: entry.document_id },
        `document:${sha256(entry.document_id)}`,
      );
      entry.current_run_id = runId;
      entry.run_ids = [...entry.run_ids, runId];
    } catch (error) {
      lastError = publicError(error);
      break;
    }
  }

  row = await saveBatch(row, {
    entries,
    status: 'running',
    lastError,
  });

  let comparisonDetails = await Promise.all(
    entries.map((entry) =>
      entry.current_run_id
        ? readRun(entry.current_run_id)
        : Promise.resolve<RunDetail>({
            connection_error: lastError || '该文献尚未创建逐篇分析任务',
          }),
    ),
  );
  const allComparisonsTerminal = comparisonDetails.every((detail) =>
    TERMINAL_RUNS.has(runStatus(detail)),
  );

  if (allComparisonsTerminal) {
    let retriesCreated = 0;
    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index];
      const detail = comparisonDetails[index];
      if (
        runSucceeded(detail)
        || entry.retry_count >= 2
        || !runRetryable(detail)
        || (entry.retry_count >= 1 && !runRejectedWith1210(detail))
      ) {
        continue;
      }
      try {
        const retryNumber = entry.retry_count + 1;
        const retryRunId = await retryModuleRun(
          row,
          entry,
          row.user_id,
          retryNumber,
        );
        entry.current_run_id = retryRunId;
        entry.run_ids = [...entry.run_ids, retryRunId];
        entry.retry_count = retryNumber;
        retriesCreated += 1;
      } catch (error) {
        lastError = publicError(error);
      }
    }
    if (retriesCreated > 0) {
      row = await saveBatch(row, {
        entries,
        status: 'retrying',
        lastError,
      });
      comparisonDetails = await Promise.all(
        entries.map((entry) => readRun(entry.current_run_id || '')),
      );
    }
  }

  const comparisonStatuses = comparisonDetails.map(runStatus);
  const comparisonTerminal = comparisonStatuses.every((status) =>
    TERMINAL_RUNS.has(status),
  );
  const comparisonSuccesses = comparisonDetails.filter(runSucceeded).length;
  const comparisonUsableCount = comparisonDetails.filter(
    comparisonHasUsableRows,
  ).length;
  let status = entries.some((entry) => !entry.current_run_id)
    ? 'creating'
    : 'running';
  if (comparisonTerminal && comparisonSuccesses === comparisonDetails.length) {
    status = 'completed';
  } else if (comparisonTerminal) {
    status = comparisonUsableCount > 0 ? 'partial' : 'failed';
  } else if (entries.some((entry) => entry.retry_count > 0)) {
    status = 'retrying';
  }

  row = await saveBatch(row, {
    entries,
    status,
    lastError,
  });

  const children = comparisonDetails.map((detail, index) =>
    childRun('I4_S_SINGLE_REFERENCE', detail, {
      documentId: entries[index]?.document_id,
      pipelineAttempts: (entries[index]?.retry_count || 0) + 1,
    }),
  );
  const queuedDocumentCount = comparisonStatuses.filter((item) => (
    item === 'queued' || item === 'created' || item === 'unknown'
  )).length;
  const runningDocumentCount = comparisonStatuses.filter((item) => (
    item === 'running'
  )).length;
  return {
    contract_version: 'v1',
    batch_id: row.id,
    investigation_id: row.investigation_id,
    claim_investigation_id: row.claim_investigation_id,
    status: row.status,
    document_count: entries.length,
    completed_document_count: comparisonDetails.filter((detail) =>
      TERMINAL_RUNS.has(runStatus(detail)),
    ).length,
    successful_document_count: comparisonUsableCount,
    aggregation_ready_document_count: comparisonSuccesses,
    queued_document_count: queuedDocumentCount,
    running_document_count: runningDocumentCount,
    incomplete_document_count: comparisonDetails.filter((detail) => (
      comparisonHasUsableRows(detail) && !runSucceeded(detail)
    )).length,
    failed_document_count: comparisonDetails.filter((detail) => (
      TERMINAL_RUNS.has(runStatus(detail)) && !comparisonHasUsableRows(detail)
    )).length,
    retrying_document_count: entries.filter(
      (entry, index) =>
        entry.retry_count === 1
        && !TERMINAL_RUNS.has(runStatus(comparisonDetails[index])),
    ).length,
    children,
    warning: row.last_error || undefined,
    created_at: row.created_at,
    updated_at: row.updated_at,
    completed_at: row.completed_at,
  };
}

type AuthorizationResult =
  | { user: AuthUser; response?: never }
  | { user?: never; response: NextResponse };

async function authorizedUser(
  request: NextRequest,
): Promise<AuthorizationResult> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return { response: createUnauthorizedResponse(request) };
  if (user.status !== 'approved') {
    return {
      response: NextResponse.json(
        { error: '账号尚未获准使用测试功能' },
        { status: 403 },
      ),
    };
  }
  return { user };
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const authorization = await authorizedUser(request);
  if (authorization.response) return authorization.response;
  const { user } = authorization;
  try {
    const body = await request.json().catch(() => ({}));
    if (!isJsonObject(body)) {
      return NextResponse.json({ error: '模块六单篇比对批次请求必须是 JSON 对象' }, { status: 400 });
    }
    const investigationId = text(body.investigation_id);
    const claimInvestigationId = text(body.claim_investigation_id);
    let ids = documentIds(body.document_ids);
    const recoverPersistedDocuments = body.recover_persisted_documents === true;
    const idempotencyKey = text(body.idempotency_key);
    if (
      !isInvalidityTestResourceId(investigationId)
      || !isInvalidityTestResourceId(claimInvestigationId)
      || !idempotencyKey
      || body.recover_persisted_comparisons === true
      || (!ids.length && !recoverPersistedDocuments)
      || ids.length > MAX_BATCH_DOCUMENTS
    ) {
      return NextResponse.json(
        { error: `模块六单篇比对批次必须提供案件、独立权利要求、1-${MAX_BATCH_DOCUMENTS} 篇全文和幂等键` },
        { status: 400 },
      );
    }
    await invalidityTestResourceOwnership().require(
      'investigation',
      investigationId,
      user.id,
      null,
    );
    if (!ids.length && recoverPersistedDocuments) {
      const recovered = await requestInvalidityService<JsonObject>(
        'test',
        (
          `/v1/lab/investigations/${encodeURIComponent(investigationId)}`
          + '/module5-inputs'
          + `?claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
        ),
      );
      ids = documentIds(recovered.document_ids);
      if (!ids.length) {
        return NextResponse.json(
          { error: '当前案件没有可恢复的已取得全文文献，请先运行模块5' },
          { status: 400 },
        );
      }
    }
    await ensureDatabaseReady();
    const keyHash = sha256(idempotencyKey);
    const manifestHash = canonicalHash({
      investigation_id: investigationId,
      claim_investigation_id: claimInvestigationId,
      document_ids: ids,
    });
    const batchId = randomUUID();
    await pgQuery(
      `
        insert into invalidity_test_module5_batches (
          id, user_id, investigation_id, claim_investigation_id,
          idempotency_key_sha256, manifest_sha256, document_ids, entries
        ) values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
        on conflict (user_id, idempotency_key_sha256) do nothing
      `,
      [
        batchId,
        user.id,
        investigationId,
        claimInvestigationId,
        keyHash,
        manifestHash,
        JSON.stringify(ids),
        JSON.stringify([]),
      ],
    );
    const row = await loadBatch(user.id, { batchId })
      || await loadBatch(user.id, { keyHash });
    if (
      !row
      || row.idempotency_key_sha256 !== keyHash
      || row.manifest_sha256 !== manifestHash
    ) {
      return NextResponse.json(
        { error: '模块六单篇比对批次幂等键已由不同输入占用' },
        { status: 409 },
      );
    }
    return NextResponse.json(await advanceBatch(row), {
      status: 202,
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    return NextResponse.json(
      { error: publicError(error) },
      { status: error instanceof InvalidityServiceError ? error.status : 500 },
    );
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const authorization = await authorizedUser(request);
  if (authorization.response) return authorization.response;
  const { user } = authorization;
  try {
    const batchId = text(request.nextUrl.searchParams.get('batch_id'));
    const investigationId = text(
      request.nextUrl.searchParams.get('investigation_id'),
    );
    const claimInvestigationId = text(
      request.nextUrl.searchParams.get('claim_investigation_id'),
    );
    if (
      batchId && !isInvalidityTestResourceId(batchId)
      || investigationId && !isInvalidityTestResourceId(investigationId)
      || claimInvestigationId && !isInvalidityTestResourceId(claimInvestigationId)
      || (!batchId && (!investigationId || !claimInvestigationId))
    ) {
      return NextResponse.json(
        { error: '必须提供有效的 batch_id 或 investigation_id' },
        { status: 400 },
      );
    }
    if (investigationId) {
      await invalidityTestResourceOwnership().require(
        'investigation',
        investigationId,
        user.id,
        null,
      );
    }
    const row = await loadBatch(user.id, {
      batchId,
      investigationId,
      claimInvestigationId,
    });
    if (!row) {
      return NextResponse.json({ error: '模块六单篇比对批次不存在' }, { status: 404 });
    }
    if (
      claimInvestigationId
      && row.claim_investigation_id !== claimInvestigationId
    ) {
      return NextResponse.json(
        { error: '模块六批次不属于当前独立权利要求' },
        { status: 409 },
      );
    }
    await invalidityTestResourceOwnership().require(
      'investigation',
      row.investigation_id,
      user.id,
      null,
    );
    return NextResponse.json(await advanceBatch(row), {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    return NextResponse.json(
      { error: publicError(error) },
      { status: error instanceof InvalidityServiceError ? error.status : 500 },
    );
  }
}
