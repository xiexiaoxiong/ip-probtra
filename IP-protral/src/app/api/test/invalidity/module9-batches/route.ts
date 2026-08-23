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

interface RunDetail {
  module_run?: JsonObject;
  job?: JsonObject;
  connection_error?: string;
}

interface RunRef {
  code: string;
  run_id: string;
  document_id?: string;
  round_iteration: number;
  is_current_attempt?: boolean;
  attempt_number?: number;
  matrix_plan?: boolean;
  reused_from_i4s_run_id?: string;
}

interface GapPlanMatrixRound {
  iteration: number;
  plan_run_id?: string;
  plan_attempt_run_ids: string[];
  primary_expressions: string[];
  primary_expression_by_feature: Record<string, string>;
  completed?: boolean;
  failed?: boolean;
  failure_reason?: string;
}

interface ReusableI4sBinding {
  fetch_run_id: string;
  qualify_run_id: string;
  i4s_run_id: string;
  document_version_id: string;
  content_sha256: string;
  date_qualification_revision: string;
  matrix_binding_sha256: string;
  input_sha256: string;
  prompt_version: string;
  rule_version: string;
}

interface GapRoundState {
  iteration: number;
  plan_run_id?: string;
  plan_attempt_run_ids: string[];
  search_runs: Record<string, string>;
  candidate_filter_run_id?: string;
  candidate_filter_attempt_run_ids: string[];
  fetch_runs: Record<string, string>;
  qualify_runs: Record<string, string>;
  i4s_runs: Record<string, string>;
  i4s_attempt_run_ids: Record<string, string[]>;
  i4s_reused_from: Record<string, string>;
  terminal_review_run_id?: string;
  completed?: boolean;
  failure_reasons: string[];
}

interface Module9State {
  rounds: GapRoundState[];
  source_module6_batch_id: string;
  source_closest_prior_art_run_id: string;
  source_obviousness_precheck_run_id: string;
  active_gap_feature_ids: string[];
  previous_query_expressions: string[];
  previous_iteration_failure_reason: string;
  resolved_gap_feature_ids: string[];
  inherited_latest_iteration: number;
  inherited_source_module_run_id?: string;
  start_mode: 'fresh' | 'resume_legacy';
  gap_query_matrix_id: string;
  gap_query_matrix_version: number;
  gap_query_matrix_status: 'planning' | 'ready' | 'superseded' | 'failed';
  gap_query_matrix_binding_sha256: string;
  gap_query_matrix_rounds: GapPlanMatrixRound[];
  i2_prompt_version: string;
  i2_rule_version: string;
  i4s_prompt_version: string;
  i4s_rule_version: string;
  reusable_i4s_bindings: Record<string, ReusableI4sBinding>;
}

interface BatchRow extends QueryResultRow {
  id: string;
  user_id: number;
  investigation_id: string;
  claim_investigation_id: string;
  idempotency_key_sha256: string;
  state: unknown;
  status: string;
  current_iteration: number;
  last_error: string | null;
  created_at: string | Date;
  updated_at: string | Date;
  completed_at: string | Date | null;
}

const TERMINAL_RUNS = new Set([
  'succeeded',
  'completed',
  'partial',
  'failed',
  'cancelled',
]);
const SUCCESS_RUNS = new Set(['succeeded', 'completed']);
const TERMINAL_BATCHES = new Set(['completed', 'exhausted', 'partial', 'failed']);
const PAUSED_BATCHES = new Set(['awaiting_next_round', 'round_partial']);
// Three model-backed attempts plus at most two deterministic frozen-fact
// attempts.  The second deterministic attempt lets an already-persisted batch
// recover after a compiler-rule deployment without repeating any provider work.
const FIRST_DETERMINISTIC_PLAN_ATTEMPT = 4;
const MAX_PLAN_ATTEMPTS = 5;
const MAX_CANDIDATE_FILTER_ATTEMPTS = 2;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter(isJsonObject) : [];
}

function record(value: unknown): JsonObject {
  return isJsonObject(value) ? value : {};
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.map(text).filter(Boolean))]
    : [];
}

function sha256(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function stableJson(value: unknown): string {
  const normalize = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(normalize);
    if (!isJsonObject(item)) return item;
    return Object.fromEntries(
      Object.keys(item).sort().map((key) => [key, normalize(item[key])]),
    );
  };
  return JSON.stringify(normalize(value));
}

function sha256Json(value: unknown): string {
  return sha256(stableJson(value));
}

function publicError(error: unknown): string {
  if (error instanceof InvalidityServiceError) return error.message;
  return error instanceof Error ? error.message : '测试服务连接中断';
}

function parseState(value: unknown): Module9State {
  const raw = isJsonObject(value) ? value : {};
  const roundsValue = Array.isArray(raw.rounds) ? raw.rounds : [];
  const rounds = roundsValue.filter(isJsonObject).map((item): GapRoundState => {
    const currentI4sRuns = idMap(item.i4s_runs);
    const attemptRuns = idListMap(item.i4s_attempt_run_ids);
    for (const [document, runId] of Object.entries(currentI4sRuns)) {
      attemptRuns[document] = [...new Set([...(attemptRuns[document] || []), runId])];
    }
    return {
      iteration: Math.max(1, Math.min(5, Number(item.iteration) || 1)),
      plan_run_id: isInvalidityTestResourceId(item.plan_run_id)
        ? item.plan_run_id
        : undefined,
      plan_attempt_run_ids: [...new Set([
        ...(Array.isArray(item.plan_attempt_run_ids)
          ? item.plan_attempt_run_ids.filter(isInvalidityTestResourceId)
          : []),
        ...(isInvalidityTestResourceId(item.plan_run_id)
          ? [item.plan_run_id]
          : []),
      ])],
      search_runs: idMap(item.search_runs),
      candidate_filter_run_id: isInvalidityTestResourceId(item.candidate_filter_run_id)
        ? item.candidate_filter_run_id
        : undefined,
      candidate_filter_attempt_run_ids: [...new Set([
        ...(Array.isArray(item.candidate_filter_attempt_run_ids)
          ? item.candidate_filter_attempt_run_ids.filter(isInvalidityTestResourceId)
          : []),
        ...(isInvalidityTestResourceId(item.candidate_filter_run_id)
          ? [item.candidate_filter_run_id]
          : []),
      ])],
      fetch_runs: idMap(item.fetch_runs),
      qualify_runs: idMap(item.qualify_runs),
      i4s_runs: currentI4sRuns,
      i4s_attempt_run_ids: attemptRuns,
      i4s_reused_from: idMap(item.i4s_reused_from),
      terminal_review_run_id: isInvalidityTestResourceId(item.terminal_review_run_id)
        ? item.terminal_review_run_id
        : undefined,
      completed: item.completed === true,
      failure_reasons: strings(item.failure_reasons),
    };
  });
  const rawMatrixRounds = Array.isArray(raw.gap_query_matrix_rounds)
    ? raw.gap_query_matrix_rounds
    : [];
  const gapQueryMatrixRounds = rawMatrixRounds
    .filter(isJsonObject)
    .map((item): GapPlanMatrixRound => ({
      iteration: Math.max(1, Math.min(5, Number(item.iteration) || 1)),
      plan_run_id: isInvalidityTestResourceId(item.plan_run_id)
        ? item.plan_run_id
        : undefined,
      plan_attempt_run_ids: [...new Set([
        ...(Array.isArray(item.plan_attempt_run_ids)
          ? item.plan_attempt_run_ids.filter(isInvalidityTestResourceId)
          : []),
        ...(isInvalidityTestResourceId(item.plan_run_id)
          ? [item.plan_run_id]
          : []),
      ])],
      primary_expressions: strings(item.primary_expressions),
      primary_expression_by_feature: isJsonObject(item.primary_expression_by_feature)
        ? Object.fromEntries(
            Object.entries(item.primary_expression_by_feature)
              .map(([featureId, expression]) => [featureId, text(expression)] as const)
              .filter(([, expression]) => Boolean(expression)),
          )
        : {},
      completed: item.completed === true,
      failed: item.failed === true,
      failure_reason: text(item.failure_reason) || undefined,
    }))
    .sort((left, right) => left.iteration - right.iteration);
  const reusableI4sBindings = isJsonObject(raw.reusable_i4s_bindings)
    ? Object.fromEntries(
        Object.entries(raw.reusable_i4s_bindings).flatMap(([document, value]) => {
          if (!isJsonObject(value)) return [];
          const binding: ReusableI4sBinding = {
            fetch_run_id: text(value.fetch_run_id),
            qualify_run_id: text(value.qualify_run_id),
            i4s_run_id: text(value.i4s_run_id),
            document_version_id: text(value.document_version_id),
            content_sha256: text(value.content_sha256),
            date_qualification_revision: text(value.date_qualification_revision),
            matrix_binding_sha256: text(value.matrix_binding_sha256),
            input_sha256: text(value.input_sha256),
            prompt_version: text(value.prompt_version),
            rule_version: text(value.rule_version),
          };
          return isInvalidityTestResourceId(binding.fetch_run_id)
            && isInvalidityTestResourceId(binding.qualify_run_id)
            && isInvalidityTestResourceId(binding.i4s_run_id)
            ? [[document, binding]]
            : [];
        }),
      )
    : {};
  const matrixStatus = text(raw.gap_query_matrix_status);
  return {
    rounds: rounds.length ? rounds : [emptyRound(1)],
    source_module6_batch_id: isInvalidityTestResourceId(raw.source_module6_batch_id)
      ? raw.source_module6_batch_id
      : '',
    source_closest_prior_art_run_id: isInvalidityTestResourceId(
      raw.source_closest_prior_art_run_id,
    )
      ? raw.source_closest_prior_art_run_id
      : '',
    source_obviousness_precheck_run_id: isInvalidityTestResourceId(
      raw.source_obviousness_precheck_run_id,
    )
      ? raw.source_obviousness_precheck_run_id
      : '',
    active_gap_feature_ids: strings(raw.active_gap_feature_ids),
    previous_query_expressions: strings(raw.previous_query_expressions),
    previous_iteration_failure_reason: text(raw.previous_iteration_failure_reason),
    resolved_gap_feature_ids: strings(raw.resolved_gap_feature_ids),
    inherited_latest_iteration: Math.max(
      0,
      Math.min(
        5,
        Number(raw.inherited_latest_iteration)
          || Math.max(0, (rounds[0]?.iteration || 1) - 1),
      ),
    ),
    inherited_source_module_run_id: isInvalidityTestResourceId(
      raw.inherited_source_module_run_id,
    )
      ? raw.inherited_source_module_run_id
      : undefined,
    start_mode: text(raw.start_mode) === 'fresh'
      ? 'fresh'
      : 'resume_legacy',
    gap_query_matrix_id: text(raw.gap_query_matrix_id),
    gap_query_matrix_version: Math.max(1, Number(raw.gap_query_matrix_version) || 1),
    gap_query_matrix_status: ['ready', 'superseded', 'failed'].includes(matrixStatus)
      ? matrixStatus as Module9State['gap_query_matrix_status']
      : 'planning',
    gap_query_matrix_binding_sha256: text(raw.gap_query_matrix_binding_sha256),
    gap_query_matrix_rounds: gapQueryMatrixRounds,
    i2_prompt_version: text(raw.i2_prompt_version),
    i2_rule_version: text(raw.i2_rule_version),
    i4s_prompt_version: text(raw.i4s_prompt_version),
    i4s_rule_version: text(raw.i4s_rule_version),
    reusable_i4s_bindings: reusableI4sBindings,
  };
}

function idMap(value: unknown): Record<string, string> {
  if (!isJsonObject(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => (
      Boolean(entry[0]) && isInvalidityTestResourceId(entry[1])
    )),
  );
}

function idListMap(value: unknown): Record<string, string[]> {
  if (!isJsonObject(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, runIds]) => [
        key,
        Array.isArray(runIds)
          ? [...new Set(runIds.filter(isInvalidityTestResourceId))]
          : [],
      ] as const)
      .filter(([, runIds]) => runIds.length),
  );
}

function emptyRound(iteration: number): GapRoundState {
  return {
    iteration,
    plan_attempt_run_ids: [],
    search_runs: {},
    candidate_filter_attempt_run_ids: [],
    fetch_runs: {},
    qualify_runs: {},
    i4s_runs: {},
    i4s_attempt_run_ids: {},
    i4s_reused_from: {},
    failure_reasons: [],
  };
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
        `select * from invalidity_test_module9_batches where id = $1 and user_id = $2 limit 1`,
        [options.batchId, userId],
      )
    : options.keyHash
      ? await pgQuery<BatchRow>(
          `select * from invalidity_test_module9_batches where idempotency_key_sha256 = $1 and user_id = $2 limit 1`,
          [options.keyHash, userId],
        )
      : options.claimInvestigationId
        ? await pgQuery<BatchRow>(
            `select * from invalidity_test_module9_batches where investigation_id = $1 and claim_investigation_id = $2 and user_id = $3 order by created_at desc limit 1`,
            [options.investigationId, options.claimInvestigationId, userId],
          )
        : await pgQuery<BatchRow>(
            `select * from invalidity_test_module9_batches where investigation_id = $1 and user_id = $2 order by created_at desc limit 1`,
            [options.investigationId, userId],
          );
  return result.rows[0] || null;
}

async function saveBatch(
  row: BatchRow,
  state: Module9State,
  status: string,
  lastError?: string | null,
): Promise<BatchRow> {
  const currentIteration = state.rounds.at(-1)?.iteration || row.current_iteration;
  const result = await pgQuery<BatchRow>(
    `
      update invalidity_test_module9_batches
      set state = $2::jsonb,
          status = $3,
          current_iteration = $4,
          last_error = $5,
          completed_at = case
            when $6 then coalesce(completed_at, now())
            else null
          end,
          updated_at = now()
      where id = $1 and user_id = $7
      returning *
    `,
    [
      row.id,
      JSON.stringify(state),
      status,
      currentIteration,
      lastError === undefined ? row.last_error : lastError,
      TERMINAL_BATCHES.has(status),
      row.user_id,
    ],
  );
  if (!result.rows[0]) throw new Error('模块九补证批次不存在');
  return result.rows[0];
}

async function createModuleRun(
  row: BatchRow,
  code: string,
  input: JsonObject,
  key: string,
): Promise<string> {
  const data = await requestInvalidityService<JsonObject>(
    'test',
    '/v1/lab/module-runs',
    {
      method: 'POST',
      body: {
        contract_version: 'v1',
        module_code: code,
        input_mode: 'live',
        investigation_id: row.investigation_id,
        claim_investigation_id: row.claim_investigation_id,
        input: { ...input, module9_batch_id: row.id },
        idempotency_key: scopedInvalidityTestIdentifier(
          'module-key',
          row.user_id,
          `module9-batch:${row.id}:${key}`,
        ),
      },
    },
  );
  const runId = text(data.module_run_id);
  if (
    !isInvalidityTestResourceId(runId)
    || text(data.investigation_id) !== row.investigation_id
  ) {
    throw new Error('测试服务返回了无效的模块九运行契约');
  }
  await invalidityTestResourceOwnership().bind({
    resourceKind: 'module_run',
    resourceId: runId,
    userId: row.user_id,
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

async function validateModule9SourceLineage({
  userId,
  investigationId,
  claimInvestigationId,
  module6BatchId,
  closestPriorArtRunId,
  obviousnessPrecheckRunId,
}: {
  userId: number;
  investigationId: string;
  claimInvestigationId: string;
  module6BatchId: string;
  closestPriorArtRunId: string;
  obviousnessPrecheckRunId: string;
}): Promise<void> {
  const module6 = await pgQuery<{
    id: string;
    investigation_id: string;
    claim_investigation_id: string;
    status: string;
  }>(
    `
      select id, investigation_id, claim_investigation_id, status
      from invalidity_test_module5_batches
      where id = $1 and user_id = $2
      limit 1
    `,
    [module6BatchId, userId],
  );
  const module6Row = module6.rows[0];
  if (
    !module6Row
    || module6Row.investigation_id !== investigationId
    || module6Row.claim_investigation_id !== claimInvestigationId
    || module6Row.status !== 'completed'
  ) {
    throw new Error('模块九必须绑定当前案件已经完成的模块6批次');
  }

  const [closestDetail, precheckDetail] = await Promise.all([
    readRun(closestPriorArtRunId),
    readRun(obviousnessPrecheckRunId),
  ]);
  const assertRun = (
    detail: RunDetail,
    runId: string,
    code: string,
    label: string,
  ): JsonObject => {
    const run = detail.module_run || {};
    if (
      !runSucceeded(detail)
      || text(run.id) !== runId
      || text(run.module_code) !== code
      || text(run.investigation_id) !== investigationId
      || text(run.claim_investigation_id) !== claimInvestigationId
    ) {
      throw new Error(`${label}不是当前案件和独立权利要求的成功运行`);
    }
    return runOutput(detail);
  };
  const closestOutput = assertRun(
    closestDetail,
    closestPriorArtRunId,
    'I4_C_CLOSEST_PRIOR_ART',
    '模块7',
  );
  if (text(closestOutput.source_module6_batch_id) !== module6BatchId) {
    throw new Error('模块7结果不属于本次模块6批次');
  }
  const precheckOutput = assertRun(
    precheckDetail,
    obviousnessPrecheckRunId,
    'I4_O_OBVIOUSNESS_PRECHECK',
    '模块8',
  );
  if (
    text(precheckOutput.source_module6_batch_id) !== module6BatchId
    || text(precheckOutput.source_closest_prior_art_run_id) !== closestPriorArtRunId
    || text(precheckOutput.closest_document_id) !== text(closestOutput.document_id)
  ) {
    throw new Error('模块8结果不属于本次模块6/模块7谱系');
  }
}

function runStatus(detail: RunDetail): string {
  return text(detail.module_run?.status)
    || text(detail.job?.status)
    || (detail.connection_error ? 'connection_interrupted' : 'unknown');
}

function runOutput(detail: RunDetail): JsonObject {
  const snapshot = detail.module_run?.output_snapshot;
  if (!isJsonObject(snapshot)) return {};
  return isJsonObject(snapshot.output) ? snapshot.output : snapshot;
}

function runSucceeded(detail: RunDetail): boolean {
  return SUCCESS_RUNS.has(runStatus(detail));
}

function documentId(output: JsonObject): string {
  return text(output.document_id)
    || text(output.stable_document_id)
    || text(output.publication_number)
    || text(output.doi)
    || text(output.url);
}

function candidateDocumentId(candidate: JsonObject): string {
  const document = record(candidate.document);
  return documentId(document)
    || text(candidate.external_id)
    || text(candidate.publication_number)
    || text(candidate.document_id);
}

function analysisReady(output: JsonObject): boolean {
  return output.analysis_ready === true || text(output.evidence_stage) === 'analysis_ready';
}

function retrieved(output: JsonObject): boolean {
  return analysisReady(output) || text(output.evidence_stage) === 'retrieved_document';
}

function child(
  code: string,
  detail: RunDetail,
  ref: RunRef,
): JsonObject {
  const run = detail.module_run || {};
  const status = runStatus(detail);
  const ok = runSucceeded(detail);
  return {
    code,
    ok,
    complete: TERMINAL_RUNS.has(status),
    status,
    output: runOutput(detail),
    moduleRunId: text(run.id) || undefined,
    documentId: ref.document_id || undefined,
    roundIteration: ref.round_iteration,
    matrixPlan: ref.matrix_plan === true,
    reusedFromI4sRunId: ref.reused_from_i4s_run_id,
    isCurrentAttempt: ref.is_current_attempt !== false,
    attemptNumber: ref.attempt_number,
    error: detail.connection_error
      || (!ok && TERMINAL_RUNS.has(status)
        ? text(run.error_message) || text(run.error_code) || status
        : undefined),
    attempts: Number(detail.job?.attempt_count) || undefined,
    raw: detail,
  };
}

function plannedQueries(output: JsonObject): JsonObject[] {
  return rows(output.queries).filter((query) => text(query.expression));
}

function queryFeatureIds(query: JsonObject): string[] {
  const gapIds = strings(query.gap_feature_ids);
  return gapIds.length ? gapIds : strings(query.feature_ids);
}

function primaryGapQueries(output: JsonObject): JsonObject[] {
  return plannedQueries(output).filter((query) => (
    text(query.provider_kind) === 'patent'
    && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
    && text(query.search_objective || 'gap_or_combination') === 'gap_or_combination'
  ));
}

function booleanOrGroups(expression: string): string[][] {
  return expression
    .split(/\s+AND\s+/i)
    .map((group) => group.trim().replace(/^\(|\)$/g, '').trim())
    .filter(Boolean)
    .map((group) => [...new Set(
      group.split(/\s+OR\s+/i).map((item) => item.trim().replace(/[()]/g, '')).filter(Boolean),
    )]);
}

function bilingual(values: string[]): boolean {
  return values.some((value) => /[\u3400-\u9fff]/.test(value))
    && values.some((value) => /[A-Za-z]/.test(value));
}

function normalizedTerm(value: unknown): string {
  return text(value).toLocaleLowerCase().replace(/[^0-9a-z\u3400-\u9fff]+/g, '');
}

function exactTargetSubjectTerms(output: JsonObject): Set<string> {
  const profile = isJsonObject(output.invention_search_profile)
    ? output.invention_search_profile
    : {};
  const coreKeys = new Set([
    ...strings(profile.subject_core_terms_zh),
    ...strings(profile.subject_core_terms_en),
  ].map(normalizedTerm).filter(Boolean));
  return new Set([
    text(output.technical_subject),
    text(profile.protected_subject),
    ...strings(profile.subject_synonyms_zh).filter((item) => !coreKeys.has(normalizedTerm(item))),
    ...strings(profile.subject_synonyms_en).filter((item) => !coreKeys.has(normalizedTerm(item))),
  ].map(normalizedTerm).filter(Boolean));
}

function validateGapPlan(output: JsonObject, expectedFeatureIds: string[] = []): void {
  const reportedUnresolved = strings(
    Array.isArray(output.allowed_gap_feature_ids)
      ? output.allowed_gap_feature_ids
      : output.uncovered_difference_feature_ids,
  );
  const unresolved = expectedFeatureIds.length
    ? expectedFeatureIds
    : reportedUnresolved;
  const unresolvedSet = new Set(unresolved);
  const queries = plannedQueries(output).filter((query) => {
    const featureIds = queryFeatureIds(query);
    return !featureIds.length || featureIds.every((featureId) => unresolvedSet.has(featureId));
  });
  const primary = primaryGapQueries(output);
  if (queries.length > 100) {
    throw new Error('模块九单轮查询数量超过安全上限');
  }
  if (primary.length !== unresolved.length) {
    throw new Error(
      `模块九主检索组数量必须等于未覆盖区别特征数量：应为 ${unresolved.length} 组，实际 ${primary.length} 组`,
    );
  }
  const featureCounts = new Map<string, number>();
  const exactSubjects = exactTargetSubjectTerms(output);
  for (const query of primary) {
    const featureIds = queryFeatureIds(query);
    if (featureIds.length !== 1 || !unresolved.includes(featureIds[0])) {
      throw new Error('模块九每个主检索组必须且只能绑定一个本轮未覆盖区别特征');
    }
    featureCounts.set(featureIds[0], (featureCounts.get(featureIds[0]) || 0) + 1);
    const groups = booleanOrGroups(text(query.expression));
    if (groups.length !== 2) {
      throw new Error('模块九主检索式必须严格采用“邻近客体 OR 组 AND 区别特征 OR 组”');
    }
    if (!groups.every(bilingual)) {
      throw new Error('模块九主检索式的客体组和区别特征组都必须同时包含中英文词');
    }
    if (groups[0].some((item) => exactSubjects.has(normalizedTerm(item)))) {
      throw new Error('模块九客体组不得使用与目标专利完全相同的产品类别');
    }
  }
  const missing = unresolved.filter((featureId) => featureCounts.get(featureId) !== 1);
  if (missing.length) {
    throw new Error(`模块九没有为每个未覆盖区别特征生成唯一主检索组：${missing.join('、')}`);
  }
  for (const query of queries) {
    if (
      text(query.query_role) !== 'gap_followup'
      || text(query.query_variant) !== 'gap_followup'
    ) {
      throw new Error('模块九计划混入非 gap 检索线');
    }
  }
}

function validVersionValue(value: unknown): boolean {
  const normalized = text(value).toLocaleLowerCase();
  return Boolean(normalized)
    && !['none', 'null', 'undefined', 'n/a', 'na'].includes(normalized);
}

function validatePlanSourceLineage(output: JsonObject, state: Module9State): void {
  if (
    text(output.source_module6_batch_id) !== state.source_module6_batch_id
    || text(output.source_closest_prior_art_run_id)
      !== state.source_closest_prior_art_run_id
    || text(output.source_obviousness_precheck_run_id)
      !== state.source_obviousness_precheck_run_id
  ) {
    throw new Error('模块九计划没有绑定本次模块6、模块7和模块8的精确运行谱系');
  }
}

function validateZeroQueryCoverage(output: JsonObject, state: Module9State): void {
  validatePlanSourceLineage(output, state);
  const decision = record(output.gap_reuse_decision);
  const coveredFeatureIds = strings(decision.covered_difference_feature_ids);
  const distinguishingFeatureIds = strings(
    rows(output.distinguishing_features).map((item) => item.feature_id),
  );
  if (
    !distinguishingFeatureIds.length
    || distinguishingFeatureIds.some((featureId) => !coveredFeatureIds.includes(featureId))
  ) {
    throw new Error('零检索计划没有逐项关闭本次D1的全部区别特征');
  }
  const precheck = record(output.obviousness_precheck);
  const precheckResolved = new Set(strings(precheck.resolved_feature_ids));
  const evidence = rows(decision.coverage_evidence);
  for (const featureId of coveredFeatureIds) {
    if (precheckResolved.has(featureId)) continue;
    const validEvidence = evidence.some((item) => {
      const reuseKey = record(item.reuse_key);
      return item.covered === true
        && text(item.feature_id) === featureId
        && text(item.source_module6_batch_id) === state.source_module6_batch_id
        && Boolean(text(item.document_id))
        && Boolean(text(item.evidence_quote))
        && Boolean(text(item.evidence_location))
        && item.same_role_or_relation === true
        && item.same_technical_effect === true
        && validVersionValue(reuseKey.document_version_id)
        && /^[0-9a-f]{64}$/i.test(text(reuseKey.content_sha256))
        && validVersionValue(reuseKey.limitation_version_id)
        && validVersionValue(reuseKey.date_qualification_revision)
        && isInvalidityTestResourceId(reuseKey.i4s_run_id)
        && text(reuseKey.i4s_rule_version) === state.i4s_rule_version;
    });
    if (!validEvidence) {
      throw new Error(
        `区别特征 ${featureId} 没有本次模块6批次的完整可引用复用证据，禁止零检索收口`,
      );
    }
  }
}

function emptyMatrixRound(iteration: number): GapPlanMatrixRound {
  return {
    iteration,
    plan_attempt_run_ids: [],
    primary_expressions: [],
    primary_expression_by_feature: {},
  };
}

function semanticExpressionKey(expression: string): string {
  return booleanOrGroups(expression)
    .map((group) => group.map(normalizedTerm).filter(Boolean).sort().join('|'))
    .join('&&');
}

function planFeatureExpressions(output: JsonObject): Record<string, string> {
  return Object.fromEntries(
    primaryGapQueries(output).flatMap((query) => {
      const featureId = queryFeatureIds(query)[0];
      const expression = text(query.expression);
      return featureId && expression ? [[featureId, expression]] : [];
    }),
  );
}

function matrixPreviousExpressions(
  state: Module9State,
  iteration: number,
): string[] {
  return [...new Set([
    ...state.previous_query_expressions,
    ...state.gap_query_matrix_rounds
      .filter((item) => item.iteration < iteration)
      .flatMap((item) => item.primary_expressions),
  ])];
}

function assertMatrixRoundChanged(
  state: Module9State,
  matrixRound: GapPlanMatrixRound,
): void {
  if (matrixRound.iteration <= 1) return;
  for (const [featureId, expression] of Object.entries(
    matrixRound.primary_expression_by_feature,
  )) {
    const currentKey = semanticExpressionKey(expression);
    const repeated = state.gap_query_matrix_rounds
      .filter((item) => item.iteration < matrixRound.iteration)
      .some((item) => (
        semanticExpressionKey(item.primary_expression_by_feature[featureId] || '')
        === currentKey
      ));
    if (currentKey && repeated) {
      throw new Error(
        `模块九第 ${matrixRound.iteration} 轮仍重复区别特征 ${featureId} 的既有检索词`,
      );
    }
  }
}

function matrixBindingPayload(
  row: BatchRow,
  state: Module9State,
  planOutputs: JsonObject[],
): JsonObject {
  const first = planOutputs.find((output) => output.matrix_plan_failed !== true) || {};
  const precheck = isJsonObject(first.obviousness_precheck)
    ? first.obviousness_precheck
    : {};
  return {
    investigation_id: row.investigation_id,
    claim_investigation_id: row.claim_investigation_id,
    source_module6_batch_id: state.source_module6_batch_id,
    source_closest_prior_art_run_id: state.source_closest_prior_art_run_id,
    source_obviousness_precheck_run_id: state.source_obviousness_precheck_run_id,
    matrix_id: state.gap_query_matrix_id,
    matrix_version: state.gap_query_matrix_version,
    closest_document_id: text(first.closest_document_id),
    feature_ids: strings(first.allowed_gap_feature_ids).length
      ? strings(first.allowed_gap_feature_ids)
      : state.active_gap_feature_ids,
    limitations: rows(first.limitations).map((item) => ({
      feature_id: text(item.feature_id),
      text: text(item.text),
    })),
    obviousness_precheck: {
      module_run_id: text(precheck.module_run_id),
      prompt_version: text(precheck.prompt_version),
      rule_version: text(precheck.rule_version),
      input_binding: isJsonObject(precheck.input_binding)
        ? precheck.input_binding
        : undefined,
    },
    i2_prompt_version: state.i2_prompt_version,
    i2_rule_version: state.i2_rule_version,
    rounds: planOutputs.map((output) => ({
      iteration: Number(output.gap_search_iteration) || 0,
      strategy: text(output.gap_search_strategy),
      failed: output.matrix_plan_failed === true,
      failure_reason: text(output.failure_reason) || undefined,
      queries: primaryGapQueries(output).map((query) => ({
        query_id: text(query.query_id),
        feature_ids: queryFeatureIds(query),
        expression: text(query.expression),
        provider_expression: text(query.provider_expression),
      })),
    })),
  };
}

async function advancePlanMatrix(
  row: BatchRow,
  state: Module9State,
): Promise<{ row: BatchRow; state: Module9State; ready: boolean }> {
  if (
    !state.source_module6_batch_id
    || !state.source_closest_prior_art_run_id
    || !state.source_obviousness_precheck_run_id
  ) {
    throw new Error('旧模块九批次缺少本次模块6/7/8谱系，请重新生成五轮批次');
  }
  if (!state.gap_query_matrix_id) state.gap_query_matrix_id = randomUUID();
  if (!state.gap_query_matrix_rounds.length) {
    const startIteration = state.rounds[0]?.iteration || 1;
    state.gap_query_matrix_rounds = Array.from(
      { length: 6 - startIteration },
      (_, index) => emptyMatrixRound(startIteration + index),
    );
    state.gap_query_matrix_status = 'planning';
  }
  if (state.gap_query_matrix_status === 'ready') {
    return { row, state, ready: true };
  }
  for (const matrixRound of state.gap_query_matrix_rounds) {
    if (matrixRound.completed) continue;
    if (!matrixRound.plan_run_id) {
      const attemptNumber = matrixRound.plan_attempt_run_ids.length + 1;
      matrixRound.plan_run_id = await createModuleRun(
        row,
        'I2_GAP_QUERY_PLAN',
        {
          module6_batch_id: state.source_module6_batch_id,
          closest_prior_art_run_id: state.source_closest_prior_art_run_id,
          obviousness_precheck_run_id: state.source_obviousness_precheck_run_id,
          gap_search_iteration: matrixRound.iteration,
          iteration_completed: false,
          max_queries: 100,
          gap_feature_ids: state.active_gap_feature_ids,
          previous_query_expressions: matrixPreviousExpressions(
            state,
            matrixRound.iteration,
          ),
          previous_iteration_failure_reason: state.previous_iteration_failure_reason,
          gap_query_matrix_id: state.gap_query_matrix_id,
          gap_query_matrix_version: state.gap_query_matrix_version,
          force_deterministic_gap_plan: (
            attemptNumber >= FIRST_DETERMINISTIC_PLAN_ATTEMPT
          ),
        },
        `matrix:${state.gap_query_matrix_version}:round:${matrixRound.iteration}:plan:attempt:${attemptNumber}`,
      );
      matrixRound.plan_attempt_run_ids.push(matrixRound.plan_run_id);
      row = await saveBatch(row, state, 'planning_matrix', null);
      return { row, state, ready: false };
    }
    const detail = await readRun(matrixRound.plan_run_id);
    if (!TERMINAL_RUNS.has(runStatus(detail))) {
      return { row, state, ready: false };
    }
    if (!runSucceeded(detail)) {
      const planError = text(detail.module_run?.error_message)
        || text(detail.module_run?.error_code)
        || 'gap 检索计划失败';
      if (matrixRound.plan_attempt_run_ids.length < MAX_PLAN_ATTEMPTS) {
        matrixRound.plan_run_id = undefined;
        state.previous_iteration_failure_reason = [
          state.previous_iteration_failure_reason,
          `矩阵第 ${matrixRound.iteration} 轮计划未通过守门：${planError}`,
        ].filter(Boolean).join('；');
        row = await saveBatch(row, state, 'planning_matrix', planError);
        return { row, state, ready: false };
      }
      matrixRound.completed = true;
      matrixRound.failed = true;
      matrixRound.failure_reason = planError;
      state.previous_iteration_failure_reason = [
        state.previous_iteration_failure_reason,
        `矩阵第 ${matrixRound.iteration} 轮计划失败并冻结：${planError}`,
      ].filter(Boolean).join('；');
      row = await saveBatch(row, state, 'planning_matrix', planError);
      continue;
    }
    const output = runOutput(detail);
    try {
      validatePlanSourceLineage(output, state);
    } catch (error) {
      const planError = publicError(error);
      matrixRound.completed = true;
      matrixRound.failed = true;
      matrixRound.failure_reason = planError;
      row = await saveBatch(row, state, 'planning_matrix', planError);
      continue;
    }
    const reportedGapFeatureIds = strings(
      Array.isArray(output.allowed_gap_feature_ids)
        ? output.allowed_gap_feature_ids
        : output.uncovered_difference_feature_ids,
    );
    if (!state.active_gap_feature_ids.length) {
      state.active_gap_feature_ids = reportedGapFeatureIds;
    }
    const decision = isJsonObject(output.gap_reuse_decision)
      ? output.gap_reuse_decision
      : {};
    if (!plannedQueries(output).length) {
      const stopReason = text(decision.stop_reason);
      if (stopReason === 'all_differences_covered') {
        try {
          validateZeroQueryCoverage(output, state);
          state.gap_query_matrix_status = 'ready';
          row = await saveBatch(row, state, 'completed', null);
        } catch (error) {
          const planError = publicError(error);
          matrixRound.completed = true;
          matrixRound.failed = true;
          matrixRound.failure_reason = planError;
          row = await saveBatch(row, state, 'planning_matrix', planError);
        }
      } else {
        const planError = '仍有未覆盖区别特征但本轮没有形成可执行查询';
        matrixRound.completed = true;
        matrixRound.failed = true;
        matrixRound.failure_reason = planError;
        row = await saveBatch(
          row,
          state,
          'planning_matrix',
          planError,
        );
      }
      if (row.status === 'completed') return { row, state, ready: false };
      continue;
    }
    try {
      validateGapPlan(output, state.active_gap_feature_ids);
      matrixRound.primary_expression_by_feature = planFeatureExpressions(output);
      matrixRound.primary_expressions = Object.values(
        matrixRound.primary_expression_by_feature,
      );
      assertMatrixRoundChanged(state, matrixRound);
    } catch (error) {
      const planError = publicError(error);
      if (matrixRound.plan_attempt_run_ids.length < MAX_PLAN_ATTEMPTS) {
        matrixRound.plan_run_id = undefined;
        state.previous_iteration_failure_reason = [
          state.previous_iteration_failure_reason,
          `矩阵第 ${matrixRound.iteration} 轮计划未通过守门：${planError}`,
        ].filter(Boolean).join('；');
        row = await saveBatch(row, state, 'planning_matrix', planError);
        return { row, state, ready: false };
      }
      matrixRound.completed = true;
      matrixRound.failed = true;
      matrixRound.failure_reason = planError;
      row = await saveBatch(row, state, 'planning_matrix', planError);
      continue;
    }
    matrixRound.completed = true;
    row = await saveBatch(row, state, 'planning_matrix', null);
  }

  const planOutputs: JsonObject[] = [];
  for (const matrixRound of state.gap_query_matrix_rounds) {
    if (matrixRound.failed) {
      planOutputs.push({
        gap_search_iteration: matrixRound.iteration,
        gap_search_strategy: `failed_round_${matrixRound.iteration}`,
        matrix_plan_failed: true,
        failure_reason: matrixRound.failure_reason,
        queries: [],
      });
      continue;
    }
    if (!matrixRound.plan_run_id) return { row, state, ready: false };
    const detail = await readRun(matrixRound.plan_run_id);
    if (!runSucceeded(detail)) return { row, state, ready: false };
    planOutputs.push(runOutput(detail));
  }
  state.gap_query_matrix_binding_sha256 = sha256Json(
    matrixBindingPayload(row, state, planOutputs),
  );
  state.gap_query_matrix_status = 'ready';
  row = await saveBatch(row, state, 'running', null);
  return { row, state, ready: true };
}

function executablePlanQueries(
  output: JsonObject,
  activeFeatureIds: string[],
): JsonObject[] {
  const active = new Set(activeFeatureIds);
  return plannedQueries(output).filter((query) => {
    const featureIds = queryFeatureIds(query);
    return !featureIds.length || featureIds.every((featureId) => active.has(featureId));
  });
}

function documentVersionId(output: JsonObject): string {
  return text(output.repository_document_version_id)
    || text(output.document_version_id)
    || text(output.version_id)
    || (documentContentSha(output) ? `sha256:${documentContentSha(output)}` : '');
}

function documentContentSha(output: JsonObject): string {
  return text(output.content_sha256)
    || text(output.document_content_sha256)
    || text(record(output.record).content_sha256);
}

function qualificationRevision(output: JsonObject): string {
  return text(output.repository_qualification_revision)
    || text(output.date_qualification_revision)
    || text(output.assessment_version)
    || text(output.repository_qualification_id)
    || text(output.qualification_id)
    || (isJsonObject(output.eligibility)
      ? `eligibility-sha256:${sha256Json(output.eligibility)}`
      : '');
}

function qualificationAllowsInventiveStep(output: JsonObject): boolean {
  return output.inventive_step_eligible === true
    || output.inventive_eligible === true
    || record(output.eligibility).inventive_step_eligible === true;
}

function i4sReuseInputPayload(
  state: Module9State,
  document: string,
  fetchOutput: JsonObject,
  qualifyOutput: JsonObject,
): JsonObject {
  return {
    document_id: document,
    document_version_id: documentVersionId(fetchOutput),
    document_content_sha256: documentContentSha(fetchOutput),
    date_qualification_revision: qualificationRevision(qualifyOutput),
    gap_query_matrix_binding_sha256: state.gap_query_matrix_binding_sha256,
    i4s_prompt_version: state.i4s_prompt_version,
    i4s_rule_version: state.i4s_rule_version,
  };
}

function reusableBindingForDocument(
  state: Module9State,
  document: string,
  fetchOutput: JsonObject,
  qualifyOutput: JsonObject,
): ReusableI4sBinding | null {
  const binding = state.reusable_i4s_bindings[document];
  if (!binding) return null;
  const currentVersion = documentVersionId(fetchOutput);
  const currentContentSha = documentContentSha(fetchOutput);
  const currentQualificationRevision = qualificationRevision(qualifyOutput);
  const currentInputSha = sha256Json(
    i4sReuseInputPayload(state, document, fetchOutput, qualifyOutput),
  );
  const required = [
    binding.i4s_run_id,
    binding.fetch_run_id,
    binding.qualify_run_id,
    binding.document_version_id,
    binding.content_sha256,
    binding.date_qualification_revision,
    binding.matrix_binding_sha256,
    binding.input_sha256,
    binding.prompt_version,
    binding.rule_version,
    currentVersion,
    currentContentSha,
    currentQualificationRevision,
    currentInputSha,
    state.gap_query_matrix_binding_sha256,
    state.i4s_prompt_version,
    state.i4s_rule_version,
  ];
  if (required.some((value) => !value)) return null;
  return binding.document_version_id === currentVersion
    && binding.content_sha256 === currentContentSha
    && binding.date_qualification_revision === currentQualificationRevision
    && binding.matrix_binding_sha256 === state.gap_query_matrix_binding_sha256
    && binding.prompt_version === state.i4s_prompt_version
    && binding.rule_version === state.i4s_rule_version
    && binding.input_sha256 === currentInputSha
    ? binding
    : null;
}

function reusableBindingForCandidate(
  state: Module9State,
  document: string,
): ReusableI4sBinding | null {
  const binding = state.reusable_i4s_bindings[document];
  if (!binding) return null;
  return binding.matrix_binding_sha256 === state.gap_query_matrix_binding_sha256
    && binding.prompt_version === state.i4s_prompt_version
    && binding.rule_version === state.i4s_rule_version
    && isInvalidityTestResourceId(binding.fetch_run_id)
    && isInvalidityTestResourceId(binding.qualify_run_id)
    && isInvalidityTestResourceId(binding.i4s_run_id)
    ? binding
    : null;
}

function resolvedFeatureIdsFromRound(
  round: GapRoundState,
  qualifyDetails: RunDetail[],
  i4sDetails: RunDetail[],
): string[] {
  const qualifiedDocuments = new Set(
    Object.keys(round.qualify_runs).filter((document, index) => {
      const detail = qualifyDetails[index];
      return detail
        && runSucceeded(detail)
        && qualificationAllowsInventiveStep(runOutput(detail));
    }),
  );
  const resolved = new Set<string>();
  Object.keys(round.i4s_runs).forEach((document, index) => {
    const detail = i4sDetails[index];
    if (!qualifiedDocuments.has(document) || !detail || !runSucceeded(detail)) return;
    for (const disclosure of rows(runOutput(detail).disclosures)) {
      if (!['explicit', 'direct_and_unambiguous', 'necessarily_implicit'].includes(
        text(disclosure.status),
      )) continue;
      if (!text(disclosure.evidence_quote) || !text(disclosure.evidence_location)) continue;
      const featureId = text(disclosure.feature_id);
      if (featureId) resolved.add(featureId);
    }
  });
  return [...resolved];
}

function rememberReusableI4sBindings(
  state: Module9State,
  round: GapRoundState,
  fetchDetails: RunDetail[],
  qualifyDetails: RunDetail[],
  i4sDetails: RunDetail[],
): void {
  const fetchByDocument = new Map<string, JsonObject>();
  const fetchRunByDocument = new Map<string, string>();
  const fetchEntries = Object.entries(round.fetch_runs);
  for (const [index, detail] of fetchDetails.entries()) {
    const output = runOutput(detail);
    const id = documentId(output);
    if (id && runSucceeded(detail)) {
      fetchByDocument.set(id, output);
      fetchRunByDocument.set(id, fetchEntries[index]?.[1] || '');
    }
  }
  const qualifyByDocument = new Map<string, JsonObject>();
  Object.keys(round.qualify_runs).forEach((document, index) => {
    const detail = qualifyDetails[index];
    if (detail && runSucceeded(detail)) qualifyByDocument.set(document, runOutput(detail));
  });
  Object.entries(round.i4s_runs).forEach(([document, runId], index) => {
    const detail = i4sDetails[index];
    if (!detail || !runSucceeded(detail) || round.i4s_reused_from[document]) return;
    const fetchOutput = fetchByDocument.get(document) || {};
    const qualifyOutput = qualifyByDocument.get(document) || {};
    const moduleRun = detail.module_run || {};
    const binding: ReusableI4sBinding = {
      fetch_run_id: fetchRunByDocument.get(document) || '',
      qualify_run_id: round.qualify_runs[document] || '',
      i4s_run_id: runId,
      document_version_id: documentVersionId(fetchOutput),
      content_sha256: documentContentSha(fetchOutput),
      date_qualification_revision: qualificationRevision(qualifyOutput),
      matrix_binding_sha256: state.gap_query_matrix_binding_sha256,
      input_sha256: sha256Json(
        i4sReuseInputPayload(
          state,
          document,
          fetchOutput,
          qualifyOutput,
        ),
      ),
      prompt_version: text(moduleRun.prompt_version),
      rule_version: text(moduleRun.rule_version)
        || text(runOutput(detail).analysis_rule_version),
    };
    if (Object.values(binding).every(Boolean)) {
      state.reusable_i4s_bindings[document] = binding;
    }
  });
}

async function finishRound(
  row: BatchRow,
  state: Module9State,
  round: GapRoundState,
  planOutput: JsonObject,
  details: RunDetail[],
  partialReason?: string,
  resolvedFeatureIds: string[] = [],
): Promise<{ row: BatchRow; state: Module9State }> {
  round.completed = true;
  const reportedGapFeatureIds = strings(
    Array.isArray(planOutput.allowed_gap_feature_ids)
      ? planOutput.allowed_gap_feature_ids
      : planOutput.uncovered_difference_feature_ids,
  );
  const resolved = new Set(resolvedFeatureIds);
  state.resolved_gap_feature_ids = [...new Set([
    ...state.resolved_gap_feature_ids,
    ...resolvedFeatureIds,
  ])];
  const activeBeforeReview = state.active_gap_feature_ids.length
    ? state.active_gap_feature_ids
    : reportedGapFeatureIds;
  state.active_gap_feature_ids = activeBeforeReview.filter(
    (featureId) => !resolved.has(featureId),
  );
  const searchDetails = details.filter((detail) => (
    ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(
      text(detail.module_run?.module_code),
    )
  ));
  const hits = searchDetails.reduce((total, detail) => {
    const output = runOutput(detail);
    return total + (Number(output.count) || rows(output.documents).length);
  }, 0);
  const successfulComparisonCount = details.filter((detail) => (
    text(detail.module_run?.module_code) === 'I4_S_SINGLE_REFERENCE'
    && runSucceeded(detail)
  )).length;
  state.previous_iteration_failure_reason = [
    `第 ${round.iteration} 轮已执行 ${Object.keys(round.search_runs).length} 条查询`,
    `原始命中 ${hits} 条`,
    `取得可分析全文 ${Object.keys(round.i4s_runs).length} 份`,
    `完成 I4-S ${successfulComparisonCount} 份`,
    partialReason ? `本轮保留失败：${partialReason}` : '',
    resolvedFeatureIds.length
      ? `本轮新增关闭 ${resolvedFeatureIds.length} 项区别特征`
      : '',
    state.active_gap_feature_ids.length
      ? `仍有 ${state.active_gap_feature_ids.length} 项区别特征未取得可引用覆盖，下一轮使用已冻结矩阵词组`
      : '全部区别特征已取得可引用覆盖',
  ].filter(Boolean).join('；');
  if (!state.active_gap_feature_ids.length) {
    row = await saveBatch(row, state, 'completed', partialReason || null);
  } else if (round.iteration < 5) {
    row = await saveBatch(row, state, 'awaiting_next_round', partialReason || null);
    ({ row, state } = await queueNextRound(row, state));
  } else {
    row = await saveBatch(row, state, 'exhausted', partialReason || null);
  }
  return { row, state };
}

async function queueNextRound(
  row: BatchRow,
  state: Module9State,
): Promise<{ row: BatchRow; state: Module9State }> {
  const round = state.rounds.at(-1);
  if (!round?.completed || round.iteration >= 5) {
    throw new Error('当前批次没有可继续的下一轮');
  }
  state.rounds.push(emptyRound(round.iteration + 1));
  row = await saveBatch(row, state, 'running', null);
  return { row, state };
}

async function advanceBatch(row: BatchRow): Promise<JsonObject> {
  let state = parseState(row.state);
  let round = state.rounds.at(-1) || emptyRound(1);
  let status = row.status;
  let lastError: string | null = null;

  try {
    if (PAUSED_BATCHES.has(row.status)) {
      return await publicBatch(row, state);
    }
    const matrixResult = await advancePlanMatrix(row, state);
    row = matrixResult.row;
    state = matrixResult.state;
    if (!matrixResult.ready || TERMINAL_BATCHES.has(row.status)) {
      return await publicBatch(row, state);
    }
    round = state.rounds.at(-1) || emptyRound(1);
    const matrixRound = state.gap_query_matrix_rounds.find(
      (item) => item.iteration === round.iteration,
    );
    if (matrixRound?.failed) {
      const failureReason = matrixRound.failure_reason
        || `模块九第 ${round.iteration} 轮检索计划失败`;
      round.failure_reasons.push(failureReason);
      round.completed = true;
      state.previous_iteration_failure_reason = [
        state.previous_iteration_failure_reason,
        `第 ${round.iteration} 轮未执行检索：${failureReason}`,
      ].filter(Boolean).join('；');
      if (round.iteration < 5) {
        row = await saveBatch(row, state, 'awaiting_next_round', failureReason);
        ({ row, state } = await queueNextRound(row, state));
      } else {
        row = await saveBatch(row, state, 'exhausted', failureReason);
      }
      return await publicBatch(row, state);
    }
    if (!matrixRound?.plan_run_id || !matrixRound.completed) {
      throw new Error(`模块九五轮矩阵缺少第 ${round.iteration} 轮冻结计划`);
    }
    round.plan_run_id = matrixRound.plan_run_id;
    const planDetail = await readRun(round.plan_run_id);
    if (!runSucceeded(planDetail)) {
      throw new Error(`模块九第 ${round.iteration} 轮冻结计划已经失效`);
    }
    const planOutput = runOutput(planDetail);
    const queries = executablePlanQueries(
      planOutput,
      state.active_gap_feature_ids,
    );
    if (!queries.length) {
      if (!state.active_gap_feature_ids.length) {
        row = await saveBatch(row, state, 'completed', null);
      } else {
        const failureReason = '当前冻结轮次没有覆盖全部仍未解决区别特征';
        round.failure_reasons.push(failureReason);
        ({ row, state } = await finishRound(
          row,
          state,
          round,
          planOutput,
          [],
          failureReason,
        ));
      }
      return await publicBatch(row, state);
    }
    for (const query of queries) {
      const queryId = text(query.query_id);
      if (!queryId || round.search_runs[queryId]) continue;
      const providerKind = text(query.provider_kind);
      const input = providerKind === 'npl'
        ? {
            query: {
              text: text(query.expression),
              subject_terms: strings(query.subject_terms),
              feature_terms: strings(query.feature_terms),
              query_id: queryId,
            },
            max_results: 10,
          }
        : {
            search_provider: 'patsnap',
            search_modality: 'text',
            query: {
              text: text(query.provider_expression) || text(query.expression),
              subject_terms: strings(query.subject_terms),
              feature_terms: strings(query.feature_terms),
            },
            subject_terms: strings(query.subject_terms),
            feature_terms: strings(query.feature_terms),
            language: text(query.language) || 'zh',
            max_results: 10,
            query_plan_query_id: queryId,
            query_plan_run_id: round.plan_run_id,
            query_role: 'gap_followup',
            query_variant: 'gap_followup',
            allow_zero_results: query.allow_zero_results === true,
            compact_fallback_allowed: false,
            query_strategy: 'gap-followup',
          };
      round.search_runs[queryId] = await createModuleRun(
        row,
        providerKind === 'npl' ? 'I3_NPL_SEARCH' : 'I3_PATENT_SEARCH',
        input,
        `round:${round.iteration}:search:${sha256(queryId)}`,
      );
    }
    row = await saveBatch(row, state, 'searching', null);
    const searchDetails = await Promise.all(
      Object.values(round.search_runs).map(readRun),
    );
    if (!searchDetails.every((detail) => TERMINAL_RUNS.has(runStatus(detail)))) {
      return await publicBatch(row, state);
    }
    const successfulSearchRunIds = Object.values(round.search_runs).filter(
      (_runId, index) => runSucceeded(searchDetails[index]),
    );
    if (!successfulSearchRunIds.length) {
      const failureReason = '本轮全部检索 provider 运行失败；本轮保留错误并继续后续轮次';
      round.failure_reasons.push(failureReason);
      ({ row, state } = await finishRound(
        row,
        state,
        round,
        planOutput,
        searchDetails,
        failureReason,
      ));
      return await publicBatch(row, state);
    }

    if (!round.candidate_filter_run_id) {
      round.candidate_filter_run_id = await createModuleRun(
        row,
        'I3_CANDIDATE_FILTER',
        {
          search_run_ids: successfulSearchRunIds,
          query_plan_run_id: round.plan_run_id,
        },
        `round:${round.iteration}:candidate-filter`,
      );
      round.candidate_filter_attempt_run_ids.push(round.candidate_filter_run_id);
      row = await saveBatch(row, state, 'filtering', null);
      return await publicBatch(row, state);
    }
    const filterDetail = await readRun(round.candidate_filter_run_id);
    if (!TERMINAL_RUNS.has(runStatus(filterDetail))) {
      return await publicBatch(row, state);
    }
    if (!runSucceeded(filterDetail)) {
      if (
        round.candidate_filter_attempt_run_ids.length
        < MAX_CANDIDATE_FILTER_ATTEMPTS
      ) {
        const attemptNumber = round.candidate_filter_attempt_run_ids.length + 1;
        round.fetch_runs = {};
        round.qualify_runs = {};
        round.i4s_runs = {};
        round.i4s_attempt_run_ids = {};
        round.candidate_filter_run_id = await createModuleRun(
          row,
          'I3_CANDIDATE_FILTER',
          {
            search_run_ids: successfulSearchRunIds,
            query_plan_run_id: round.plan_run_id,
          },
          `round:${round.iteration}:candidate-filter:attempt:${attemptNumber}`,
        );
        round.candidate_filter_attempt_run_ids.push(
          round.candidate_filter_run_id,
        );
        row = await saveBatch(row, state, 'filtering', null);
        return await publicBatch(row, state);
      }
      const failureReason = text(filterDetail.module_run?.error_message)
        || '候选清理失败；本轮保留错误并继续后续轮次';
      round.failure_reasons.push(failureReason);
      ({ row, state } = await finishRound(
        row,
        state,
        round,
        planOutput,
        [...searchDetails, filterDetail],
        failureReason,
      ));
      return await publicBatch(row, state);
    }
    const candidates = rows(runOutput(filterDetail).fetch_candidates);
    for (const candidate of candidates) {
      const candidateId = text(candidate.candidate_id);
      if (!candidateId || round.fetch_runs[candidateId]) continue;
      const candidateDocument = candidateDocumentId(candidate);
      const reusable = candidateDocument
        ? reusableBindingForCandidate(state, candidateDocument)
        : null;
      if (reusable) {
        round.fetch_runs[candidateId] = reusable.fetch_run_id;
        round.qualify_runs[candidateDocument] = reusable.qualify_run_id;
        continue;
      }
      round.fetch_runs[candidateId] = await createModuleRun(
        row,
        'I3_FETCH',
        {
          candidate_filter_run_id: round.candidate_filter_run_id,
          candidate_id: candidateId,
        },
        `round:${round.iteration}:fetch:${sha256(candidateId)}`,
      );
    }
    row = await saveBatch(row, state, 'fetching', null);
    if (!Object.keys(round.fetch_runs).length) {
      ({ row, state } = await finishRound(row, state, round, planOutput, searchDetails));
      return await publicBatch(row, state);
    }
    const fetchEntries = Object.entries(round.fetch_runs);
    const fetchDetails = await Promise.all(fetchEntries.map(([, runId]) => readRun(runId)));
    if (!fetchDetails.every((detail) => TERMINAL_RUNS.has(runStatus(detail)))) {
      return await publicBatch(row, state);
    }

    for (let index = 0; index < fetchEntries.length; index += 1) {
      const detail = fetchDetails[index];
      const output = runOutput(detail);
      const id = documentId(output);
      if (!runSucceeded(detail) || !id || !retrieved(output) || round.qualify_runs[id]) continue;
      round.qualify_runs[id] = await createModuleRun(
        row,
        'I3_QUALIFY',
        { document_id: id },
        `round:${round.iteration}:qualify:${sha256(id)}`,
      );
    }
    row = await saveBatch(row, state, 'qualifying', null);
    const qualifyDetails = await Promise.all(
      Object.values(round.qualify_runs).map(readRun),
    );
    if (!qualifyDetails.every((detail) => TERMINAL_RUNS.has(runStatus(detail)))) {
      return await publicBatch(row, state);
    }

    const fetchByDocument = new Map<string, JsonObject>();
    for (const detail of fetchDetails) {
      const output = runOutput(detail);
      const id = documentId(output);
      if (runSucceeded(detail) && id) fetchByDocument.set(id, output);
      if (!runSucceeded(detail) || !id || !analysisReady(output) || round.i4s_runs[id]) continue;
      const qualifyEntries = Object.entries(round.qualify_runs);
      const qualifyIndex = qualifyEntries.findIndex(([document]) => document === id);
      const qualifyDetail = qualifyIndex >= 0 ? qualifyDetails[qualifyIndex] : undefined;
      const qualifyOutput = qualifyDetail ? runOutput(qualifyDetail) : {};
      if (
        !qualifyDetail
        || !runSucceeded(qualifyDetail)
        || !qualificationAllowsInventiveStep(qualifyOutput)
      ) continue;
      const reusable = reusableBindingForDocument(
        state,
        id,
        fetchByDocument.get(id) || output,
        qualifyOutput,
      );
      if (reusable) {
        round.i4s_runs[id] = reusable.i4s_run_id;
        round.i4s_attempt_run_ids[id] = [reusable.i4s_run_id];
        round.i4s_reused_from[id] = reusable.i4s_run_id;
        continue;
      }
      round.i4s_runs[id] = await createModuleRun(
        row,
        'I4_S_SINGLE_REFERENCE',
        i4sReuseInputPayload(
          state,
          id,
          fetchByDocument.get(id) || output,
          qualifyOutput,
        ),
        `round:${round.iteration}:i4s:${sha256(id)}`,
      );
      round.i4s_attempt_run_ids[id] = [round.i4s_runs[id]];
    }
    row = await saveBatch(row, state, 'comparing', null);
    const i4sDetails = await Promise.all(Object.values(round.i4s_runs).map(readRun));
    if (!i4sDetails.every((detail) => TERMINAL_RUNS.has(runStatus(detail)))) {
      return await publicBatch(row, state);
    }
    rememberReusableI4sBindings(
      state,
      round,
      fetchDetails,
      qualifyDetails,
      i4sDetails,
    );
    const resolvedFeatureIds = resolvedFeatureIdsFromRound(
      round,
      qualifyDetails,
      i4sDetails,
    );
    const failedComparisons = Object.entries(round.i4s_runs).filter(
      ([,], index) => !runSucceeded(i4sDetails[index]),
    );
    if (failedComparisons.length) {
      const failureReason = `${failedComparisons.length} 份文献 I4-S 未成功，已完成结果保持不变`;
      round.failure_reasons.push(failureReason);
      ({ row, state } = await finishRound(
        row,
        state,
        round,
        planOutput,
        [...searchDetails, ...fetchDetails, ...qualifyDetails, ...i4sDetails],
        failureReason,
        resolvedFeatureIds,
      ));
      return await publicBatch(row, state);
    }
    const incompletePipelineRuns = [
      ...searchDetails,
      ...fetchDetails,
      ...qualifyDetails,
      ...i4sDetails,
    ].filter((detail) => !runSucceeded(detail));
    const pipelineFailureReason = incompletePipelineRuns.length
      ? `${incompletePipelineRuns.length} 条取文/日期核验/单篇比对运行未完整成功，已保留错误并继续后续轮次`
      : undefined;
    if (pipelineFailureReason) round.failure_reasons.push(pipelineFailureReason);
    ({ row, state } = await finishRound(
      row,
      state,
      round,
      planOutput,
      [...searchDetails, ...fetchDetails, ...qualifyDetails, ...i4sDetails],
      pipelineFailureReason,
      resolvedFeatureIds,
    ));
  } catch (error) {
    lastError = publicError(error);
    status = error instanceof InvalidityServiceError ? 'partial' : 'failed';
    row = await saveBatch(row, state, status, lastError);
  }
  return await publicBatch(row, state);
}

async function publicBatch(row: BatchRow, state: Module9State): Promise<JsonObject> {
  const refs: RunRef[] = [];
  for (const matrixRound of state.gap_query_matrix_rounds) {
    const planRunIds = [...new Set([
      ...matrixRound.plan_attempt_run_ids,
      ...(matrixRound.plan_run_id ? [matrixRound.plan_run_id] : []),
    ])];
    for (const [index, runId] of planRunIds.entries()) {
      refs.push({
        code: 'I2_GAP_QUERY_PLAN',
        run_id: runId,
        round_iteration: matrixRound.iteration,
        is_current_attempt: runId === matrixRound.plan_run_id,
        attempt_number: index + 1,
        matrix_plan: true,
      });
    }
  }
  for (const round of state.rounds) {
    for (const runId of Object.values(round.search_runs)) {
      refs.push({ code: 'I3_SEARCH', run_id: runId, round_iteration: round.iteration });
    }
    const filterRunIds = [...new Set([
      ...round.candidate_filter_attempt_run_ids,
      ...(round.candidate_filter_run_id ? [round.candidate_filter_run_id] : []),
    ])];
    for (const [index, runId] of filterRunIds.entries()) {
      refs.push({
        code: 'I3_CANDIDATE_FILTER',
        run_id: runId,
        round_iteration: round.iteration,
        is_current_attempt: runId === round.candidate_filter_run_id,
        attempt_number: index + 1,
      });
    }
    for (const runId of Object.values(round.fetch_runs)) {
      refs.push({ code: 'I3_FETCH', run_id: runId, round_iteration: round.iteration });
    }
    for (const [document, runId] of Object.entries(round.qualify_runs)) {
      refs.push({
        code: 'I3_QUALIFY',
        run_id: runId,
        document_id: document,
        round_iteration: round.iteration,
      });
    }
    for (const [document, currentRunId] of Object.entries(round.i4s_runs)) {
      const attemptRunIds = round.i4s_attempt_run_ids[document]?.length
        ? round.i4s_attempt_run_ids[document]
        : [currentRunId];
      for (const [index, runId] of attemptRunIds.entries()) {
        refs.push({
          code: 'I4_S_SINGLE_REFERENCE',
          run_id: runId,
          document_id: document,
          round_iteration: round.iteration,
          is_current_attempt: runId === currentRunId,
          attempt_number: index + 1,
          reused_from_i4s_run_id: round.i4s_reused_from[document],
        });
      }
    }
    if (round.terminal_review_run_id) {
      refs.push({
        code: 'I2_GAP_QUERY_PLAN',
        run_id: round.terminal_review_run_id,
        round_iteration: round.iteration,
      });
    }
  }
  const details = await Promise.all(refs.map((ref) => readRun(ref.run_id)));
  const children = details.map((detail, index) => {
    const ref = refs[index];
    const actualCode = text(detail.module_run?.module_code) || ref.code;
    return child(actualCode, detail, ref);
  });
  const currentRound = state.rounds.at(-1);
  const roundSummaries = state.rounds.map((round) => {
    const roundChildren = children.filter((item) => (
      Number(item.roundIteration) === round.iteration
    ));
    const currentChildren = roundChildren.filter((item) => item.isCurrentAttempt !== false);
    const currentPlan = [...currentChildren].reverse().find((item) => (
      text(item.code) === 'I2_GAP_QUERY_PLAN'
      && text(isJsonObject(item.output) ? item.output.gap_search_iteration : '') !== ''
      && isJsonObject(item.output)
      && item.output.iteration_completed !== true
    )) || [...currentChildren].reverse().find((item) => (
      text(item.code) === 'I2_GAP_QUERY_PLAN'
      && isJsonObject(item.output)
      && item.output.iteration_completed !== true
    ));
    const queries = currentPlan && isJsonObject(currentPlan.output)
      ? plannedQueries(currentPlan.output)
      : [];
    const searches = currentChildren.filter((item) => (
      ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(text(item.code))
    ));
    const fetches = currentChildren.filter((item) => text(item.code) === 'I3_FETCH');
    const qualifications = currentChildren.filter((item) => text(item.code) === 'I3_QUALIFY');
    const comparisons = currentChildren.filter((item) => (
      text(item.code) === 'I4_S_SINGLE_REFERENCE'
    ));
    return {
      iteration: round.iteration,
      status: round.completed
        ? 'completed'
        : round === currentRound
          ? row.status
          : 'pending',
      completed: round.completed === true,
      planned_query_count: queries.length,
      executed_search_count: searches.filter((item) => item.ok === true).length,
      search_run_count: searches.length,
      raw_hit_count: searches.filter((item) => item.ok === true).reduce(
        (total, item) => total + (
          Number(isJsonObject(item.output) ? item.output.count : 0)
          || rows(isJsonObject(item.output) ? item.output.documents : []).length
        ),
        0,
      ),
      fetch_run_count: fetches.length,
      analysis_ready_document_count: fetches.filter((item) => (
        item.ok === true && isJsonObject(item.output) && analysisReady(item.output)
      )).length,
      qualified_document_count: qualifications.filter((item) => item.ok === true).length,
      comparison_count: comparisons.length,
      successful_comparison_count: comparisons.filter((item) => item.ok === true).length,
      failed_comparison_count: comparisons.filter((item) => (
        item.complete === true && item.ok !== true
      )).length,
      children: roundChildren,
    };
  });
  const uniqueAnalysisDocuments = new Set(
    state.rounds.flatMap((item) => Object.keys(item.i4s_runs)),
  );
  const matrixRoundSummaries = state.gap_query_matrix_rounds.map((matrixRound) => {
    const planChild = [...children].reverse().find((item) => (
      item.matrixPlan === true
      && item.isCurrentAttempt !== false
      && Number(item.roundIteration) === matrixRound.iteration
      && text(item.code) === 'I2_GAP_QUERY_PLAN'
    ));
    const output = planChild && isJsonObject(planChild.output)
      ? planChild.output
      : {};
    const executionRound = state.rounds.find(
      (item) => item.iteration === matrixRound.iteration,
    );
    const featureTextById = new Map(
      rows(output.limitations).map((item) => [
        text(item.feature_id),
        text(item.text),
      ]),
    );
    const matrixQueries = plannedQueries(output).map((query) => {
      const featureIds = queryFeatureIds(query);
      const queryId = text(query.query_id);
      const searchRunId = executionRound?.search_runs[queryId];
      const featureResolved = featureIds.length > 0 && featureIds.every(
        (featureId) => state.resolved_gap_feature_ids.includes(featureId),
      );
      return {
        query_id: queryId,
        feature_ids: featureIds,
        feature_text: featureIds.map((featureId) => featureTextById.get(featureId))
          .filter(Boolean)
          .join('；'),
        expression: text(query.expression),
        provider_expression: text(query.provider_expression),
        query_role: text(query.query_role),
        provider_kind: text(query.provider_kind),
        execution_status: searchRunId
          ? 'submitted'
          : featureResolved
            ? 'skipped_feature_resolved'
            : executionRound?.completed
              ? 'not_executed'
              : 'pending',
        search_run_id: searchRunId,
      };
    });
    return {
      iteration: matrixRound.iteration,
      status: matrixRound.failed
        ? 'failed'
        : matrixRound.completed
        ? 'frozen'
        : matrixRound.plan_run_id
          ? runStatus(details[refs.findIndex((ref) => ref.run_id === matrixRound.plan_run_id)])
          : 'pending',
      strategy: text(output.gap_search_strategy),
      strategy_label: text(output.gap_search_strategy_label),
      strategy_description: text(output.gap_search_strategy_description),
      plan_run_id: matrixRound.plan_run_id,
      plan_attempt_count: matrixRound.plan_attempt_run_ids.length,
      failure_reason: matrixRound.failure_reason,
      queries: matrixQueries,
    };
  });
  const failureLedger = [
    ...state.gap_query_matrix_rounds.flatMap((matrixRound) => (
      matrixRound.failed && matrixRound.failure_reason
        ? [{
            gap_search_iteration: matrixRound.iteration,
            stage: 'I2_GAP_QUERY_PLAN',
            error_code: 'PLAN_ROUND_FAILED',
            safe_summary: matrixRound.failure_reason,
            affected_feature_ids: state.active_gap_feature_ids,
          }]
        : []
    )),
    ...state.rounds.flatMap((round) => round.failure_reasons.map((reason) => ({
      gap_search_iteration: round.iteration,
      stage: 'MODULE9_ROUND',
      error_code: 'ROUND_PARTIAL',
      safe_summary: reason,
      affected_feature_ids: state.active_gap_feature_ids,
    }))),
    ...children.flatMap((item) => (
      item.complete === true && item.ok !== true
        ? [{
            gap_search_iteration: Number(item.roundIteration) || 0,
            stage: text(item.code) || 'MODULE9_RUN',
            error_code: text(record(record(item.raw).module_run).error_code)
              || 'RUN_INCOMPLETE',
            safe_summary: text(item.error) || text(item.status) || '运行未完整成功',
            affected_feature_ids: state.active_gap_feature_ids,
          }]
        : []
    )),
  ];
  return {
    contract_version: 'v1',
    batch_id: row.id,
    investigation_id: row.investigation_id,
    claim_investigation_id: row.claim_investigation_id,
    source_module6_batch_id: state.source_module6_batch_id,
    source_closest_prior_art_run_id: state.source_closest_prior_art_run_id,
    source_obviousness_precheck_run_id: state.source_obviousness_precheck_run_id,
    status: row.status,
    current_iteration: currentRound?.iteration || row.current_iteration,
    completed_round_count: state.rounds.filter((item) => item.completed).length,
    planned_query_count: roundSummaries.reduce(
      (total, item) => total + Number(item.planned_query_count),
      0,
    ),
    analysis_ready_document_count: uniqueAnalysisDocuments.size,
    inherited_latest_iteration: state.inherited_latest_iteration,
    started_from_history: state.inherited_latest_iteration > 0,
    start_mode: state.start_mode,
    resolved_gap_feature_ids: state.resolved_gap_feature_ids,
    gap_query_matrix: {
      matrix_id: state.gap_query_matrix_id,
      version: state.gap_query_matrix_version,
      status: state.gap_query_matrix_status,
      binding_sha256: state.gap_query_matrix_binding_sha256,
      rounds: matrixRoundSummaries,
    },
    failure_ledger: failureLedger,
    rounds: roundSummaries,
    current_round: roundSummaries.at(-1),
    can_continue: row.status === 'awaiting_next_round',
    can_continue_with_failures: row.status === 'round_partial',
    can_retry_failed: row.status === 'round_partial'
      && (roundSummaries.at(-1)?.failed_comparison_count || 0) > 0,
    continuation_reason: row.status === 'completed'
      ? '全部区别特征已取得可引用覆盖证据，无需继续检索'
      : row.status === 'exhausted'
        ? '已经完成第 5 个 gap 轮，达到本案自动补证上限'
        : row.status === 'partial' || row.status === 'failed'
          ? row.last_error || '当前批次未形成可继续的完整轮次'
          : undefined,
    children,
    warning: row.last_error || undefined,
    created_at: row.created_at,
    updated_at: row.updated_at,
    completed_at: row.completed_at,
  };
}

async function retryFailedComparisons(row: BatchRow): Promise<JsonObject> {
  const state = parseState(row.state);
  const round = state.rounds.at(-1);
  if (!round || row.status !== 'round_partial') {
    throw new Error('当前模块九批次没有可单独重试的失败文献');
  }
  const entries = Object.entries(round.i4s_runs);
  const details = await Promise.all(entries.map(([, runId]) => readRun(runId)));
  const failedDocuments = entries.filter((_, index) => !runSucceeded(details[index]));
  if (!failedDocuments.length) {
    throw new Error('当前轮没有失败的 I4-S 文献');
  }
  for (const [document] of failedDocuments) {
    const attempts = round.i4s_attempt_run_ids[document] || [round.i4s_runs[document]];
    const attemptNumber = attempts.length + 1;
    const retryRunId = await createModuleRun(
      row,
      'I4_S_SINGLE_REFERENCE',
      { document_id: document },
      `round:${round.iteration}:i4s:${sha256(document)}:retry:${attemptNumber}`,
    );
    round.i4s_runs[document] = retryRunId;
    round.i4s_attempt_run_ids[document] = [...new Set([...attempts, retryRunId])];
    delete round.i4s_reused_from[document];
  }
  row = await saveBatch(row, state, 'comparing', null);
  return await advanceBatch(row);
}

async function continueBatch(
  row: BatchRow,
  allowFailed: boolean,
): Promise<JsonObject> {
  let state = parseState(row.state);
  const round = state.rounds.at(-1);
  if (!round) throw new Error('模块九批次缺少当前轮');
  if (row.status === 'round_partial') {
    if (!allowFailed) {
      throw new Error('当前轮仍有失败文献；请先重试，或明确选择带失败继续下一轮');
    }
    if (!round.plan_run_id) throw new Error('当前轮缺少可复用的 gap 检索计划');
    const planDetail = await readRun(round.plan_run_id);
    if (!runSucceeded(planDetail)) throw new Error('当前轮 gap 检索计划不可用');
    const searchDetails = await Promise.all(Object.values(round.search_runs).map(readRun));
    const fetchDetails = await Promise.all(Object.values(round.fetch_runs).map(readRun));
    const qualifyDetails = await Promise.all(Object.values(round.qualify_runs).map(readRun));
    const i4sDetails = await Promise.all(Object.values(round.i4s_runs).map(readRun));
    const details = [
      ...searchDetails,
      ...fetchDetails,
      ...qualifyDetails,
      ...i4sDetails,
    ];
    const failedCount = i4sDetails.filter((detail) => !runSucceeded(detail)).length;
    rememberReusableI4sBindings(
      state,
      round,
      fetchDetails,
      qualifyDetails,
      i4sDetails,
    );
    const resolvedFeatureIds = resolvedFeatureIdsFromRound(
      round,
      qualifyDetails,
      i4sDetails,
    );
    ({ row, state } = await finishRound(
      row,
      state,
      round,
      runOutput(planDetail),
      details,
      `${failedCount} 份 I4-S 文献未成功，按律师明确选择继续`,
      resolvedFeatureIds,
    ));
  } else if (row.status !== 'awaiting_next_round') {
    throw new Error('当前模块九批次尚未到达可继续下一轮的状态');
  }
  if (round.iteration < 5) {
    ({ row, state } = await queueNextRound(row, state));
  }
  return await advanceBatch(row);
}

type AuthorizationResult =
  | { user: AuthUser; response?: never }
  | { user?: never; response: NextResponse };

async function authorizedUser(request: NextRequest): Promise<AuthorizationResult> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return { response: createUnauthorizedResponse(request) };
  if (user.status !== 'approved') {
    return {
      response: NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 }),
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
      return NextResponse.json({ error: '模块九补证批次请求必须是 JSON 对象' }, { status: 400 });
    }
    const action = text(body.action);
    if (action) {
      const batchId = text(body.batch_id);
      if (!isInvalidityTestResourceId(batchId)) {
        return NextResponse.json({ error: '模块九批次操作必须提供有效的 batch_id' }, { status: 400 });
      }
      const actionRow = await loadBatch(user.id, { batchId });
      if (!actionRow) {
        return NextResponse.json({ error: '模块九补证批次不存在' }, { status: 404 });
      }
      await invalidityTestResourceOwnership().require(
        'investigation',
        actionRow.investigation_id,
        user.id,
        null,
      );
      const result = action === 'retry_failed'
        ? await retryFailedComparisons(actionRow)
        : action === 'continue_next_round'
          ? await continueBatch(actionRow, body.allow_failed === true)
          : null;
      if (!result) {
        return NextResponse.json({ error: '不支持的模块九批次操作' }, { status: 400 });
      }
      return NextResponse.json(result, {
        status: 202,
        headers: { 'Cache-Control': 'no-store' },
      });
    }
    const investigationId = text(body.investigation_id);
    const claimInvestigationId = text(body.claim_investigation_id);
    const sourceModule6BatchId = text(body.module6_batch_id);
    const sourceClosestPriorArtRunId = text(body.closest_prior_art_run_id);
    const sourceObviousnessPrecheckRunId = text(body.obviousness_precheck_run_id);
    const idempotencyKey = text(body.idempotency_key);
    const requestedStartMode = text(body.start_mode);
    const startMode = requestedStartMode === 'fresh' ? 'fresh' : 'resume_legacy';
    if (
      !isInvalidityTestResourceId(investigationId)
      || !isInvalidityTestResourceId(claimInvestigationId)
      || !isInvalidityTestResourceId(sourceModule6BatchId)
      || !isInvalidityTestResourceId(sourceClosestPriorArtRunId)
      || !isInvalidityTestResourceId(sourceObviousnessPrecheckRunId)
      || !idempotencyKey
      || (requestedStartMode && !['fresh', 'resume_legacy'].includes(requestedStartMode))
    ) {
      return NextResponse.json({ error: '模块九补证批次必须提供案件、独立权利要求、有效起点和幂等键' }, { status: 400 });
    }
    await invalidityTestResourceOwnership().require(
      'investigation',
      investigationId,
      user.id,
      null,
    );
    await ensureDatabaseReady();
    await validateModule9SourceLineage({
      userId: user.id,
      investigationId,
      claimInvestigationId,
      module6BatchId: sourceModule6BatchId,
      closestPriorArtRunId: sourceClosestPriorArtRunId,
      obviousnessPrecheckRunId: sourceObviousnessPrecheckRunId,
    });
    const keyHash = sha256(idempotencyKey);
    const priorBatchResult = await pgQuery<BatchRow>(
      `
        select * from invalidity_test_module9_batches
        where user_id = $1
          and investigation_id = $2
          and claim_investigation_id = $3
        order by created_at desc
        limit 1
      `,
      [user.id, investigationId, claimInvestigationId],
    );
    // The compatibility cursor is only for the first durable batch created
    // after the old browser-led flow.  Once any durable batch exists, a new
    // test batch must start from round 1 instead of inheriting the latest
    // I2-G run created by another durable batch and appearing to jump to 5/5.
    const cursor = startMode === 'fresh' || priorBatchResult.rows.length
      ? {
          latest_iteration: 0,
          next_iteration: 1,
          active_gap_feature_ids: [],
          previous_query_expressions: [],
          previous_iteration_failure_reason: '',
          source_module_run_id: null,
        }
      : await requestInvalidityService<JsonObject>(
          'test',
          (
            `/v1/lab/investigations/${encodeURIComponent(investigationId)}`
            + '/module9-cursor'
            + `?claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
          ),
        );
    const startIteration = Math.max(
      1,
      Math.min(5, Number(cursor.next_iteration) || 1),
    );
    const health = await requestInvalidityService<JsonObject>('test', '/health');
    const i2PromptVersion = text(health.i2_prompt_version);
    const i2RuleVersion = text(health.i2_rule_version);
    const i4sPromptVersion = text(health.i4s_prompt_version);
    const i4sRuleVersion = text(health.i4s_rule_version);
    if (
      !i2PromptVersion
      || !i2RuleVersion
      || !i4sPromptVersion
      || !i4sRuleVersion
    ) {
      throw new Error('测试服务没有公开模块九所需的当前 I2/I4-S 版本，已拒绝生成不可复用矩阵');
    }
    const batchId = randomUUID();
    const initialState: Module9State = {
      rounds: [emptyRound(startIteration)],
      source_module6_batch_id: sourceModule6BatchId,
      source_closest_prior_art_run_id: sourceClosestPriorArtRunId,
      source_obviousness_precheck_run_id: sourceObviousnessPrecheckRunId,
      active_gap_feature_ids: strings(cursor.active_gap_feature_ids),
      previous_query_expressions: strings(cursor.previous_query_expressions),
      previous_iteration_failure_reason: text(
        cursor.previous_iteration_failure_reason,
      ),
      resolved_gap_feature_ids: [],
      inherited_latest_iteration: Math.max(
        0,
        Math.min(5, Number(cursor.latest_iteration) || 0),
      ),
      inherited_source_module_run_id: isInvalidityTestResourceId(
        cursor.source_module_run_id,
      )
        ? cursor.source_module_run_id
        : undefined,
      start_mode: startMode,
      gap_query_matrix_id: randomUUID(),
      gap_query_matrix_version: 1,
      gap_query_matrix_status: 'planning',
      gap_query_matrix_binding_sha256: '',
      gap_query_matrix_rounds: [],
      i2_prompt_version: i2PromptVersion,
      i2_rule_version: i2RuleVersion,
      i4s_prompt_version: i4sPromptVersion,
      i4s_rule_version: i4sRuleVersion,
      reusable_i4s_bindings: {},
    };
    await pgQuery(
      `
        insert into invalidity_test_module9_batches (
          id, user_id, investigation_id, claim_investigation_id,
          idempotency_key_sha256, state, current_iteration
        ) values ($1, $2, $3, $4, $5, $6::jsonb, $7)
        on conflict (user_id, idempotency_key_sha256) do nothing
      `,
      [
        batchId,
        user.id,
        investigationId,
        claimInvestigationId,
        keyHash,
        JSON.stringify(initialState),
        startIteration,
      ],
    );
    const row = await loadBatch(user.id, { batchId })
      || await loadBatch(user.id, { keyHash });
    const persistedState = row ? parseState(row.state) : null;
    if (
      !row
      || row.investigation_id !== investigationId
      || row.claim_investigation_id !== claimInvestigationId
      || persistedState?.source_module6_batch_id !== sourceModule6BatchId
      || persistedState?.source_closest_prior_art_run_id !== sourceClosestPriorArtRunId
      || persistedState?.source_obviousness_precheck_run_id
        !== sourceObviousnessPrecheckRunId
    ) {
      return NextResponse.json({ error: '模块九补证批次幂等键已由不同输入占用' }, { status: 409 });
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
    const investigationId = text(request.nextUrl.searchParams.get('investigation_id'));
    const claimInvestigationId = text(
      request.nextUrl.searchParams.get('claim_investigation_id'),
    );
    if (
      (batchId && !isInvalidityTestResourceId(batchId))
      || (investigationId && !isInvalidityTestResourceId(investigationId))
      || (claimInvestigationId && !isInvalidityTestResourceId(claimInvestigationId))
      || (!batchId && !investigationId)
    ) {
      return NextResponse.json({ error: '必须提供有效的 batch_id 或 investigation_id' }, { status: 400 });
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
      return NextResponse.json({ error: '模块九补证批次不存在' }, { status: 404 });
    }
    await invalidityTestResourceOwnership().require(
      'investigation',
      row.investigation_id,
      user.id,
      null,
    );
    if (
      (investigationId && row.investigation_id !== investigationId)
      || (claimInvestigationId && row.claim_investigation_id !== claimInvestigationId)
    ) {
      return NextResponse.json({ error: '模块九批次不属于当前案件或独立权利要求' }, { status: 409 });
    }
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
