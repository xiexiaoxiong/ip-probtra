import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import { pgQuery } from '@/lib/postgres';
import type { AnalysisStep, StepStatus } from '@/lib/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type JsonObject = Record<string, unknown>;
type DiagnosticStatus = 'idle' | 'running' | 'completed' | 'error';
type DiagnosticStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

function numberValue(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function textValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : value == null ? '' : String(value).trim();
}

function elapsedMs(steps: AnalysisStep[]): number | undefined {
  const started = steps.map((step) => step.startedAt).filter((value): value is number => typeof value === 'number');
  const completed = steps.map((step) => step.completedAt).filter((value): value is number => typeof value === 'number');
  if (!started.length) return undefined;
  const end = completed.length ? Math.max(...completed) : Date.now();
  return Math.max(0, end - Math.min(...started));
}

function diagnosticStatus(status: StepStatus): DiagnosticStatus {
  if (status === 'completed') return 'completed';
  if (status === 'error') return 'error';
  if (status === 'running' || status === 'partial' || status === 'waiting_input') return 'running';
  return 'idle';
}

function keywordStageStatus(routingStep: AnalysisStep | undefined, keywordStep: AnalysisStep | undefined): StepStatus {
  if (routingStep?.status === 'error' || keywordStep?.status === 'error') return 'error';
  if (keywordStep?.status === 'completed') return 'completed';
  if (
    routingStep?.status === 'running'
    || routingStep?.status === 'partial'
    || routingStep?.status === 'waiting_input'
    || keywordStep?.status === 'running'
    || keywordStep?.status === 'partial'
    || keywordStep?.status === 'waiting_input'
  ) return 'running';
  return 'pending';
}

function payloadStatus(params: {
  status: StepStatus;
  sessionStatus: string;
  upstreamFailed: boolean;
}): DiagnosticStepStatus {
  if (params.status === 'completed') return 'completed';
  if (params.status === 'error') return 'failed';
  if (params.status === 'running' || params.status === 'partial' || params.status === 'waiting_input') return 'running';
  if (params.sessionStatus === 'error' && params.upstreamFailed) return 'skipped';
  return 'pending';
}

function stepSnapshot(step: AnalysisStep | undefined): JsonObject | null {
  if (!step) return null;
  return {
    stepId: step.id,
    stepName: step.name,
    status: step.status,
    error: step.error || null,
    startedAt: step.startedAt ? new Date(step.startedAt).toISOString() : null,
    completedAt: step.completedAt ? new Date(step.completedAt).toISOString() : null,
  };
}

function stageError(steps: Array<AnalysisStep | undefined>, fallback = ''): string {
  return steps.map((step) => textValue(step?.error)).find(Boolean) || fallback;
}

async function loadPatentRecord(patentRecordId: number | null): Promise<{
  record: JsonObject | null;
  claims: JsonObject[];
  figures: JsonObject[];
}> {
  if (!patentRecordId) return { record: null, claims: [], figures: [] };
  const [recordResult, claimsResult, figuresResult] = await Promise.all([
    pgQuery(
      `SELECT id, task_id, patent_number, patent_holder, title, abstract_text,
              application_date, priority_date, specification, parse_errors,
              created_at, updated_at
       FROM patent_parse_records
       WHERE id = $1
       LIMIT 1`,
      [patentRecordId],
    ),
    pgQuery(
      `SELECT id, claim_id, claim_type, claim_text, parent_claim_id, sentence_units, created_at
       FROM patent_claims
       WHERE record_id = $1
       ORDER BY id ASC`,
      [patentRecordId],
    ),
    pgQuery(
      `SELECT id, figure_id, figure_url, figure_description, storage_key,
              file_path, mime_type, file_size, file_sha256, created_at
       FROM patent_figures
       WHERE record_id = $1
       ORDER BY id ASC`,
      [patentRecordId],
    ),
  ]);
  return {
    record: (recordResult.rows[0] as JsonObject | undefined) || null,
    claims: claimsResult.rows as JsonObject[],
    figures: figuresResult.rows as JsonObject[],
  };
}

async function loadKeywordEvidence(params: {
  sessionId: string;
  patentRecordId: number | null;
  preferredRunId: number | null;
}): Promise<{ runs: JsonObject[]; selectedRun: JsonObject | null; rows: JsonObject[] }> {
  if (!params.patentRecordId) return { runs: [], selectedRun: null, rows: [] };
  const runResult = await pgQuery(
    `SELECT id, patent_record_id, analysis_session_id, workflow_variant,
            keywords_count, created_at, updated_at
     FROM keyword_runs
     WHERE analysis_session_id = $1
       AND patent_record_id = $2
     ORDER BY created_at DESC, id DESC
     LIMIT 20`,
    [params.sessionId, params.patentRecordId],
  );
  const runs = runResult.rows as JsonObject[];
  const selectedRun = runs.find((row) => numberValue(row.id) === params.preferredRunId) || runs[0] || null;
  const selectedRunId = numberValue(selectedRun?.id);
  if (!selectedRunId) return { runs, selectedRun, rows: [] };
  const rowsResult = await pgQuery(
    `SELECT id, keyword_run_id, patent_record_id, analysis_session_id, keyword_id,
            claim_id, keyword_text, keyword_type, source_location, generation_method,
            confidence_score, raw_payload, created_at
     FROM keyword_records
     WHERE analysis_session_id = $1
       AND patent_record_id = $2
       AND keyword_run_id = $3
     ORDER BY id ASC
     LIMIT 200`,
    [params.sessionId, params.patentRecordId, selectedRunId],
  );
  return { runs, selectedRun, rows: rowsResult.rows as JsonObject[] };
}

async function loadProductEvidence(params: {
  sessionId: string;
  patentRecordId: number | null;
  preferredRunId: number | null;
}): Promise<{
  runs: JsonObject[];
  selectedRun: JsonObject | null;
  products: JsonObject[];
  candidates: JsonObject[];
  candidateSummary: JsonObject[];
}> {
  if (!params.patentRecordId) {
    return { runs: [], selectedRun: null, products: [], candidates: [], candidateSummary: [] };
  }
  const runResult = await pgQuery(
    `SELECT id, patent_record_id, analysis_session_id, product_dataset_id, status,
            source_keyword_count, candidate_link_count, accepted_products_count,
            rejected_candidates_count, platforms_queried, provider, error_message,
            started_at, finished_at, raw_payload, created_at, updated_at
     FROM product_detail_search_runs
     WHERE analysis_session_id = $1
       AND patent_record_id = $2
     ORDER BY created_at DESC, id DESC
     LIMIT 20`,
    [params.sessionId, params.patentRecordId],
  );
  const runs = runResult.rows as JsonObject[];
  const selectedRun = runs.find((row) => numberValue(row.id) === params.preferredRunId) || runs[0] || null;
  const selectedRunId = numberValue(selectedRun?.id);
  if (!selectedRunId) {
    return { runs, selectedRun, products: [], candidates: [], candidateSummary: [] };
  }
  const [productsResult, candidatesResult, summaryResult] = await Promise.all([
    pgQuery(
      `SELECT id, run_id, product_id, platform, product_name, product_url, final_url,
              price, sales, brand, manufacturer, matched_keywords, description,
              detail_text, picture, source_text_url, source_image_url, quality_score,
              quality_flags, created_at
       FROM product_detail_search_products
       WHERE run_id = $1
         AND analysis_session_id = $2
         AND patent_record_id = $3
       ORDER BY id ASC
       LIMIT 100`,
      [selectedRunId, params.sessionId, params.patentRecordId],
    ),
    pgQuery(
      `SELECT id, run_id, keyword_text, platform, candidate_url, final_url, title,
              status, rejection_reason, quality_score, created_at
       FROM product_detail_search_candidates
       WHERE run_id = $1
         AND analysis_session_id = $2
         AND patent_record_id = $3
       ORDER BY id ASC
       LIMIT 200`,
      [selectedRunId, params.sessionId, params.patentRecordId],
    ),
    pgQuery(
      `SELECT status, COALESCE(rejection_reason, '') AS rejection_reason, COUNT(*)::int AS count
       FROM product_detail_search_candidates
       WHERE run_id = $1
         AND analysis_session_id = $2
         AND patent_record_id = $3
       GROUP BY status, rejection_reason
       ORDER BY status ASC, count DESC, rejection_reason ASC
       LIMIT 100`,
      [selectedRunId, params.sessionId, params.patentRecordId],
    ),
  ]);
  return {
    runs,
    selectedRun,
    products: productsResult.rows as JsonObject[],
    candidates: candidatesResult.rows as JsonObject[],
    candidateSummary: summaryResult.rows as JsonObject[],
  };
}

async function loadComparisonEvidence(params: {
  sessionId: string;
  patentRecordId: number | null;
  preferredRunIds: number[];
}): Promise<{
  runs: JsonObject[];
  selectedRun: JsonObject | null;
  rows: JsonObject[];
  productCount: number;
  featureCount: number;
  llmErrorCount: number;
}> {
  if (!params.patentRecordId) {
    return { runs: [], selectedRun: null, rows: [], productCount: 0, featureCount: 0, llmErrorCount: 0 };
  }
  const runResult = await pgQuery(
    `SELECT id, patent_record_id, analysis_session_id, run_id, status, error_message,
            result_summary, product_count, started_at, finished_at, created_at, updated_at
     FROM claim_compare_runs
     WHERE analysis_session_id = $1
       AND patent_record_id = $2
     ORDER BY created_at DESC, id DESC
     LIMIT 20`,
    [params.sessionId, params.patentRecordId],
  );
  const runs = runResult.rows as JsonObject[];
  const selectedRun = params.preferredRunIds
    .map((runId) => runs.find((row) => numberValue(row.id) === runId))
    .find(Boolean) || runs[0] || null;
  const selectedRunId = numberValue(selectedRun?.id);
  if (!selectedRunId) {
    return { runs, selectedRun, rows: [], productCount: 0, featureCount: 0, llmErrorCount: 0 };
  }
  const [rowsResult, countResult] = await Promise.all([
    pgQuery(
      `SELECT id, claim_compare_run_id, product_id, product_name, claim_id, feature_id,
              feature_text, comparison_result, similarity_score, score_band,
              feature_full_score, feature_awarded_score, feature_effective_length,
              matched_effective_length, claim_total_effective_length, zeroed_by_mismatch,
              reason, reasoning_type, evidence, evidence_images, token_units, created_at
       FROM claim_compare_results
       WHERE claim_compare_run_id = $1
         AND analysis_session_id = $2
         AND patent_record_id = $3
       ORDER BY product_name ASC, claim_id ASC, feature_id ASC, id ASC
       LIMIT 500`,
      [selectedRunId, params.sessionId, params.patentRecordId],
    ),
    pgQuery(
      `SELECT COUNT(DISTINCT product_id)::int AS product_count,
              COUNT(*)::int AS feature_count,
              COUNT(*) FILTER (
                WHERE COALESCE(reason, '') LIKE '%大模型调用失败%'
                   OR COALESCE(reason, '') LIKE '%模型调用失败%'
                   OR COALESCE(reason, '') LIKE '%规则兜底%'
                   OR COALESCE(evidence, '') LIKE '%大模型调用失败%'
                   OR COALESCE(evidence, '') LIKE '%规则兜底%'
              )::int AS llm_error_count
       FROM claim_compare_results
       WHERE claim_compare_run_id = $1
         AND analysis_session_id = $2
         AND patent_record_id = $3`,
      [selectedRunId, params.sessionId, params.patentRecordId],
    ),
  ]);
  const counts = (countResult.rows[0] as JsonObject | undefined) || {};
  return {
    runs,
    selectedRun,
    rows: rowsResult.rows as JsonObject[],
    productCount: Number(counts.product_count || 0),
    featureCount: Number(counts.feature_count || 0),
    llmErrorCount: Number(counts.llm_error_count || 0),
  };
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved' || !isAdmin(user)) {
    return NextResponse.json({ error: '仅管理员可以查看同任务专利分析诊断数据' }, { status: 403 });
  }

  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'infringement') {
    return NextResponse.json({ error: '专利分析会话不存在' }, { status: 404 });
  }

  try {
    const patentRecordId = numberValue(session.results?.dbRecordId);
    const preferredKeywordRunId = numberValue(session.results?.keywordRunId);
    const preferredProductRunId = numberValue(session.results?.searchRunId);
    const preferredComparisonRunIds = [
      session.results?.finalClaimCompareRunId,
      session.results?.claimCompareRunId,
      session.results?.initialClaimCompareRunId,
    ].map(numberValue).filter((value): value is number => value !== null);
    const [patentEvidence, keywordEvidence, productEvidence, comparisonEvidence] = await Promise.all([
      loadPatentRecord(patentRecordId),
      loadKeywordEvidence({ sessionId: session.id, patentRecordId, preferredRunId: preferredKeywordRunId }),
      loadProductEvidence({ sessionId: session.id, patentRecordId, preferredRunId: preferredProductRunId }),
      loadComparisonEvidence({ sessionId: session.id, patentRecordId, preferredRunIds: preferredComparisonRunIds }),
    ]);

    const workflowStep = (stepId: number) => session.steps.find((step) => step.id === stepId);
    const module1Step = workflowStep(1);
    const routingStep = workflowStep(2);
    const module2Step = workflowStep(3);
    const module3Step = workflowStep(4);
    const module4Step = workflowStep(5);
    const summaryStep = workflowStep(6);
    const module2Status = keywordStageStatus(routingStep, module2Step);
    const upstream1Failed = module1Step?.status === 'error';
    const upstream2Failed = upstream1Failed || module2Status === 'error';
    const upstream3Failed = upstream2Failed || module3Step?.status === 'error';
    const patentRecord = patentEvidence.record || {};
    const keywordRun = keywordEvidence.selectedRun || {};
    const productRun = productEvidence.selectedRun || {};
    const comparisonRun = comparisonEvidence.selectedRun || {};

    const module1Error = stageError([module1Step]);
    const module2Error = stageError(
      [routingStep, module2Step],
      textValue(session.results?.module2Exception),
    );
    const module3Error = stageError(
      [module3Step],
      textValue(session.results?.module3Exception) || textValue(productRun.error_message),
    );
    const module4Error = stageError(
      [module4Step],
      textValue(session.results?.module4Exception) || textValue(comparisonRun.error_message),
    );

    const module1Payload: JsonObject = {
      status: payloadStatus({ status: module1Step?.status || 'pending', sessionStatus: session.status, upstreamFailed: false }),
      elapsedMs: elapsedMs(module1Step ? [module1Step] : []),
      patentRecordId: patentRecordId || undefined,
      taskId: textValue(patentRecord.task_id) || undefined,
      runId: textValue(session.results?.module1RunId) || undefined,
      inputType: session.input.type,
      inputLabel: session.input.fileName || session.input.value,
      claimsCount: patentEvidence.claims.length,
      figuresCount: patentEvidence.figures.length,
      metadata: {
        title: patentRecord.title || session.patentTitle,
        patent_number: patentRecord.patent_number || session.patentNumber,
        patent_holder: patentRecord.patent_holder,
        abstract: patentRecord.abstract_text,
        application_date: patentRecord.application_date,
        priority_date: patentRecord.priority_date,
      },
      finalOutput: {
        metadata: {
          title: patentRecord.title || session.patentTitle,
          patent_number: patentRecord.patent_number || session.patentNumber,
          patent_holder: patentRecord.patent_holder,
          abstract: patentRecord.abstract_text,
          application_date: patentRecord.application_date,
          priority_date: patentRecord.priority_date,
        },
        claims: patentEvidence.claims,
        figures: patentEvidence.figures,
        specification: patentRecord.specification,
        parse_errors: patentRecord.parse_errors,
      },
      errorMessage: module1Error,
      raw: {
        lineage: { analysisSessionId: session.id, patentRecordId, source: 'analysis_sessions.results.dbRecordId' },
        workflowSteps: [stepSnapshot(module1Step)],
        patentRecord: patentEvidence.record,
      },
    };

    const keywordTexts = keywordEvidence.rows.map((row) => textValue(row.keyword_text)).filter(Boolean);
    const module2Payload: JsonObject = {
      status: payloadStatus({ status: module2Status, sessionStatus: session.status, upstreamFailed: upstream1Failed }),
      elapsedMs: elapsedMs([routingStep, module2Step].filter((step): step is AnalysisStep => Boolean(step))),
      industry: session.results?.industryUsed || session.results?.detectedIndustry || keywordRun.workflow_variant || 'general',
      industryReasoning: session.results?.industryReasoning || '',
      keywordRunId: numberValue(keywordRun.id) || preferredKeywordRunId || undefined,
      keywordsCount: keywordEvidence.rows.length,
      keywords: keywordEvidence.rows,
      errorMessage: module2Error || (module2Status === 'completed' && keywordEvidence.rows.length === 0 ? '模块2已结束但没有找到本 session 的关键词记录' : ''),
      raw: {
        lineage: { analysisSessionId: session.id, patentRecordId, keywordRunId: numberValue(keywordRun.id) },
        workflowSteps: [stepSnapshot(routingStep), stepSnapshot(module2Step)],
        selectedRun: keywordEvidence.selectedRun,
        runHistory: keywordEvidence.runs,
      },
    };

    const productKeywords = Array.isArray(productRun.raw_payload)
      ? productRun.raw_payload
      : productRun.raw_payload && typeof productRun.raw_payload === 'object'
        ? (productRun.raw_payload as JsonObject).keywords
        : keywordTexts;
    const module3Payload: JsonObject = {
      status: payloadStatus({ status: module3Step?.status || 'pending', sessionStatus: session.status, upstreamFailed: upstream2Failed }),
      elapsedMs: elapsedMs(module3Step ? [module3Step] : []),
      productDetailSearchRunId: numberValue(productRun.id) || preferredProductRunId || undefined,
      acceptedProductsCount: Number(productRun.accepted_products_count || productEvidence.products.length || 0),
      totalCandidateLinksCount: Number(productRun.candidate_link_count || productEvidence.candidates.length || 0),
      rejectedCandidatesCount: Number(productRun.rejected_candidates_count || 0),
      keywords: productKeywords,
      products: productEvidence.products,
      candidateSummary: productEvidence.candidateSummary,
      candidatesPreview: productEvidence.candidates,
      errorMessage: module3Error,
      raw: {
        lineage: { analysisSessionId: session.id, patentRecordId, productDetailSearchRunId: numberValue(productRun.id) },
        workflowSteps: [stepSnapshot(module3Step)],
        selectedRun: productEvidence.selectedRun,
        runHistory: productEvidence.runs,
        candidateSummary: productEvidence.candidateSummary,
        candidatesPreview: productEvidence.candidates,
      },
    };

    const module4Payload: JsonObject = {
      status: payloadStatus({ status: module4Step?.status || 'pending', sessionStatus: session.status, upstreamFailed: upstream3Failed }),
      elapsedMs: elapsedMs(module4Step ? [module4Step] : []),
      claimCompareRunId: numberValue(comparisonRun.id) || preferredComparisonRunIds[0] || undefined,
      resultSummary: textValue(comparisonRun.result_summary),
      productCount: comparisonEvidence.productCount,
      featureCount: comparisonEvidence.featureCount,
      llmErrorCount: comparisonEvidence.llmErrorCount,
      rows: comparisonEvidence.rows,
      errorMessage: module4Error || (
        module4Step?.status === 'pending' && upstream3Failed
          ? '未执行：本次分析在上一阶段失败后停止。'
          : ''
      ),
      raw: {
        lineage: { analysisSessionId: session.id, patentRecordId, claimCompareRunId: numberValue(comparisonRun.id) },
        workflowSteps: [stepSnapshot(module4Step), stepSnapshot(summaryStep)],
        selectedRun: comparisonEvidence.selectedRun,
        runHistory: comparisonEvidence.runs,
      },
    };

    return NextResponse.json(
      {
        diagnostic_mode: 'same_task_read_only',
        readOnly: true,
        session: {
          id: session.id,
          status: session.status,
          pipelineVersion: session.pipelineVersion,
          patentTitle: session.patentTitle || patentRecord.title || null,
          patentNumber: session.patentNumber || patentRecord.patent_number || null,
          createdAt: new Date(session.createdAt).toISOString(),
          updatedAt: new Date(session.updatedAt).toISOString(),
          workflowSteps: session.steps.map(stepSnapshot),
          errors: session.steps.map((step) => textValue(step.error)).filter(Boolean),
        },
        patentRecordId,
        inputs: {
          module1: {
            type: session.input.type,
            value: session.input.value,
            fileName: session.input.fileName,
            fileUrl: session.input.fileUrl,
            text: session.input.text,
            textLength: session.input.text?.length || 0,
          },
          module2: {
            patentRecordId,
            analysisSessionId: session.id,
            industry: session.results?.industryUsed || session.results?.detectedIndustry || keywordRun.workflow_variant || 'general',
            industryReasoning: session.results?.industryReasoning || '',
          },
          module3: {
            patentRecordId,
            analysisSessionId: session.id,
            keywordRunId: numberValue(keywordRun.id),
            keywords: productKeywords,
            platforms: productRun.platforms_queried || [],
            provider: productRun.provider || null,
            sourceKeywordCount: productRun.source_keyword_count || keywordTexts.length,
          },
          module4: {
            patentRecordId,
            analysisSessionId: session.id,
            productDetailSearchRunId: numberValue(productRun.id),
            acceptedProductsCount: productEvidence.products.length,
            products: productEvidence.products.map((product) => ({
              id: product.id,
              name: product.product_name,
              url: product.final_url || product.product_url,
            })),
          },
        },
        modules: {
          module1: { status: diagnosticStatus(module1Step?.status || 'pending'), result: module1Payload, error: module1Error || null },
          module2: { status: diagnosticStatus(module2Status), result: module2Payload, error: module2Error || null },
          module3: { status: diagnosticStatus(module3Step?.status || 'pending'), result: module3Payload, error: module3Error || null },
          module4: { status: diagnosticStatus(module4Step?.status || 'pending'), result: module4Payload, error: module4Error || null },
        },
      },
      {
        headers: {
          'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
          Pragma: 'no-cache',
          Expires: '0',
        },
      },
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '读取管理员专利分析诊断数据失败' },
      { status: 500 },
    );
  }
}
