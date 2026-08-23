import { randomUUID } from 'crypto';
import { mkdir, writeFile } from 'fs/promises';
import path from 'path';
import { NextRequest, NextResponse } from 'next/server';
import { module2ConfigKeyForIndustry } from '@/lib/industry-keyword-flow';
import { postJsonWithTimeout } from '@/lib/long-running-http';
import { pgQuery } from '@/lib/postgres';
import { getUploadsDir } from '@/lib/runtime-paths';
import { canonicalServiceUrl, requireTestUser } from '@/lib/test-route-guard';
import type { IndustryType } from '@/lib/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';
export const maxDuration = 1800;

export type PipelineAction = 'patentParse' | 'keywords' | 'productSearch' | 'claimCompare' | 'all';
type StepStatus = 'completed' | 'failed' | 'skipped';

export type PatentInput = {
  type?: 'url' | 'file' | 'text';
  url?: string;
  fileUrl?: string;
  fileName?: string;
  text?: string;
};

export type PipelineRequest = {
  action?: PipelineAction;
  patentInput?: PatentInput;
  patentRecordId?: number;
  analysisSessionId?: string;
  industry?: IndustryType;
  manualKeywords?: string[];
  platforms?: string[];
  maxKeywords?: number;
  maxCandidatesPerKeyword?: number;
  maxDetailCandidates?: number;
  maxProducts?: number;
  requestTimeoutSeconds?: number;
  serpUrlLimit?: number;
};

type ModuleConfig = {
  url: string;
  token?: string;
};

type JsonObject = Record<string, unknown>;

const DEFAULT_PLATFORMS = ['jd', '1688'];
const EXAMPLE_TASK_PREFIX = 'module1_abstract_examples_1782749163_';

const KEYWORD_PRIORITY: Record<string, number> = {
  OBJECT_BASE: 0,
  REQUIRED_FEATURE: 1,
  INVENTION: 2,
  COMBINED: 3,
};

function getModuleConfigs(): Record<'module1' | 'module2' | 'module2Fitness' | 'module2HomeAppliances' | 'productDetail' | 'module4', ModuleConfig> {
  return {
    module1: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_MODULE1_API_URL', ['TEST_MODULE1_API_URL', 'MODULE1_API_URL'], 'http://127.0.0.1:5101/run'),
      token: process.env.PATENT_ANALYSIS_MODULE1_API_TOKEN || process.env.TEST_MODULE1_API_TOKEN || process.env.MODULE1_API_TOKEN || undefined,
    },
    module2: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_MODULE2_API_URL', ['TEST_MODULE2_API_URL', 'MODULE2_API_URL'], 'http://127.0.0.1:5102/run'),
      token: process.env.PATENT_ANALYSIS_MODULE2_API_TOKEN || process.env.TEST_MODULE2_API_TOKEN || process.env.MODULE2_API_TOKEN || undefined,
    },
    module2Fitness: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_MODULE2_FITNESS_API_URL', ['TEST_MODULE2_FITNESS_API_URL', 'MODULE2_FITNESS_API_URL'], 'http://127.0.0.1:5103/run'),
      token: process.env.PATENT_ANALYSIS_MODULE2_FITNESS_API_TOKEN || process.env.TEST_MODULE2_FITNESS_API_TOKEN || process.env.MODULE2_FITNESS_API_TOKEN || undefined,
    },
    module2HomeAppliances: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_URL', ['TEST_MODULE2_HOME_APPLIANCES_API_URL', 'MODULE2_HOME_APPLIANCES_API_URL'], 'http://127.0.0.1:5104/run'),
      token: process.env.PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_TOKEN || process.env.TEST_MODULE2_HOME_APPLIANCES_API_TOKEN || process.env.MODULE2_HOME_APPLIANCES_API_TOKEN || undefined,
    },
    productDetail: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_PRODUCT_SEARCH_API_URL', ['TEST_PRODUCT_DETAIL_MODULE3_API_URL', 'PRODUCT_DETAIL_MODULE3_API_URL', 'PRODUCT_SEARCH_API_URL'], 'http://127.0.0.1:5107/run'),
      token: process.env.PATENT_ANALYSIS_PRODUCT_SEARCH_API_TOKEN || process.env.TEST_PRODUCT_DETAIL_MODULE3_API_TOKEN || process.env.PRODUCT_DETAIL_MODULE3_API_TOKEN || process.env.PRODUCT_SEARCH_API_TOKEN || undefined,
    },
    module4: {
      url: canonicalServiceUrl('PATENT_ANALYSIS_MODULE4_API_URL', ['TEST_MODULE4_API_URL', 'MODULE4_API_URL'], 'http://127.0.0.1:5106/run'),
      token: process.env.PATENT_ANALYSIS_MODULE4_API_TOKEN || process.env.TEST_MODULE4_API_TOKEN || process.env.MODULE4_API_TOKEN || undefined,
    },
  };
}

async function resolvePatentInput(input: PatentInput | undefined): Promise<{
  patentFileUrl: string;
  inputType: 'url' | 'file' | 'text';
  inputLabel: string;
}> {
  const inputType = input?.type;
  if (inputType === 'url') {
    const patentFileUrl = readText(input?.url);
    if (!patentFileUrl) throw new Error('缺少专利 URL');
    return { patentFileUrl, inputType, inputLabel: patentFileUrl };
  }
  if (inputType === 'file') {
    const patentFileUrl = readText(input?.fileUrl);
    if (!patentFileUrl) throw new Error('缺少上传后的专利文件路径');
    return { patentFileUrl, inputType, inputLabel: readText(input?.fileName) || '已上传专利文件' };
  }
  if (inputType === 'text') {
    const patentText = readText(input?.text);
    if (!patentText) throw new Error('缺少专利文本');
    const uploadsDir = getUploadsDir();
    await mkdir(uploadsDir, { recursive: true });
    const patentFileUrl = path.join(
      uploadsDir,
      `product-pipeline-${Date.now()}-${randomUUID()}.txt`,
    );
    await writeFile(patentFileUrl, patentText, 'utf-8');
    return {
      patentFileUrl,
      inputType,
      inputLabel: `粘贴文本（${patentText.length} 字）`,
    };
  }
  throw new Error('请选择上传文件、输入网址或粘贴专利文本');
}

function createHeaders(token?: string): Record<string, string> {
  return token
    ? {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
    : {
        'Content-Type': 'application/json',
      };
}

function normalizeKeywords(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    const text = String(item || '').trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

function positiveInteger(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function readText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : value == null ? '' : String(value).trim();
}

function normalizeProductUrlKey(value: unknown): string {
  const text = readText(value);
  if (!text) return '';
  try {
    const url = new URL(text);
    const trackingKeys = new Set([
      'spm', 'scm', 'source', 'ref', 'ref_', 'from', 'pvid', 'clickid',
      'campaign', 'campaignid', 'adid', 'affiliate', 'affid',
    ]);
    const keepParams = new URLSearchParams();
    const entries = [...url.searchParams.entries()]
      .filter(([key]) => !key.toLowerCase().startsWith('utm_') && !trackingKeys.has(key.toLowerCase()))
      .sort(([leftKey, leftValue], [rightKey, rightValue]) =>
        leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue),
      );
    for (const [key, item] of entries) {
      keepParams.append(key, item);
    }
    url.hash = '';
    url.search = keepParams.toString();
    url.hostname = url.hostname.toLowerCase();
    return url.toString().replace(/\/$/, '');
  } catch {
    return text.split('#', 1)[0].trim().toLowerCase().replace(/\/$/, '');
  }
}

function productDedupeKey(row: JsonObject): string {
  const urlKey = normalizeProductUrlKey(row.final_url) || normalizeProductUrlKey(row.product_url);
  if (urlKey) return `url:${urlKey}`;
  const platform = readText(row.platform).toLowerCase();
  const name = readText(row.product_name).replace(/\s+/g, '').toLowerCase();
  return name ? `title:${platform}:${name}` : '';
}

function countDuplicateProductRows(rows: JsonObject[]): number {
  const seen = new Set<string>();
  let duplicates = 0;
  for (const row of rows) {
    const key = productDedupeKey(row);
    if (!key) continue;
    if (seen.has(key)) {
      duplicates += 1;
      continue;
    }
    seen.add(key);
  }
  return duplicates;
}

function moduleFailed(result: JsonObject): boolean {
  const status = readText(result.status).toLowerCase();
  return status === 'failed' || status === 'error';
}

function moduleErrorMessage(result: JsonObject): string {
  return readText(result.error_message) || readText(result.error) || readText(result.message);
}

async function callModule(config: ModuleConfig, payload: JsonObject, timeoutMs: number): Promise<JsonObject> {
  const response = await postJsonWithTimeout(config.url, payload, {
    headers: createHeaders(config.token),
    timeoutMs,
  });
  let data: JsonObject;
  try {
    data = JSON.parse(response.text) as JsonObject;
  } catch {
    data = { raw: response.text };
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${JSON.stringify(data).slice(0, 500)}`);
  }
  return data;
}

async function loadKeywords(params: {
  patentRecordId: number;
  analysisSessionId: string;
  keywordRunId?: number;
}): Promise<JsonObject[]> {
  const values: unknown[] = [params.patentRecordId];
  let where = 'patent_record_id = $1';
  if (params.keywordRunId) {
    values.push(params.keywordRunId);
    where += ` AND keyword_run_id = $${values.length}`;
  } else if (params.analysisSessionId) {
    values.push(params.analysisSessionId);
    where += ` AND analysis_session_id = $${values.length}`;
  }

  const result = await pgQuery(
    `
      SELECT id, keyword_run_id, keyword_text, keyword_type, source_location,
             generation_method, confidence_score, raw_payload, created_at
      FROM keyword_records
      WHERE ${where}
      ORDER BY
        CASE keyword_type
          WHEN 'OBJECT_BASE' THEN 0
          WHEN 'REQUIRED_FEATURE' THEN 1
          WHEN 'COMBINED' THEN 2
          ELSE 3
        END,
        id ASC
      LIMIT 100
    `,
    values,
  );
  return result.rows as JsonObject[];
}

function keywordTexts(rows: JsonObject[], limit: number): string[] {
  const seen = new Set<string>();
  return rows
    .map((row) => readText(row.keyword_text))
    .filter((keyword) => {
      if (!keyword || seen.has(keyword)) return false;
      seen.add(keyword);
      return true;
    })
    .slice(0, limit);
}

async function loadCandidateSummary(productDetailSearchRunId?: number): Promise<JsonObject[]> {
  if (!productDetailSearchRunId) return [];
  const result = await pgQuery(
    `
      SELECT status, COALESCE(rejection_reason, '') AS rejection_reason, COUNT(*)::int AS count
      FROM product_detail_search_candidates
      WHERE run_id = $1
      GROUP BY status, rejection_reason
      ORDER BY status ASC, count DESC, rejection_reason ASC
      LIMIT 80
    `,
    [productDetailSearchRunId],
  );
  return result.rows as JsonObject[];
}

async function loadProductDetailProducts(params: {
  patentRecordId: number;
  analysisSessionId: string;
  productDetailSearchRunId?: number;
}): Promise<JsonObject[]> {
  const values: unknown[] = [params.patentRecordId];
  let where = 'patent_record_id = $1';
  if (params.productDetailSearchRunId) {
    values.push(params.productDetailSearchRunId);
    where += ` AND run_id = $${values.length}`;
  } else if (params.analysisSessionId) {
    values.push(params.analysisSessionId);
    where += ` AND analysis_session_id = $${values.length}`;
  }
  const result = await pgQuery(
    `
      SELECT id, run_id, platform, product_name, product_url, final_url, price,
             brand, manufacturer, matched_keywords, description, picture,
             quality_score, quality_flags, created_at
      FROM product_detail_search_products
      WHERE ${where}
      ORDER BY created_at DESC, id DESC
      LIMIT 50
    `,
    values,
  );
  return result.rows as JsonObject[];
}

async function loadClaimCompareResults(claimCompareRunId?: number): Promise<{
  rows: JsonObject[];
  productCount: number;
  featureCount: number;
  llmErrorCount: number;
}> {
  if (!claimCompareRunId) {
    return { rows: [], productCount: 0, featureCount: 0, llmErrorCount: 0 };
  }
  const [rowsResult, countResult] = await Promise.all([
    pgQuery(
      `
        SELECT product_id, product_name, claim_id, feature_id, comparison_result,
               similarity_score, score_band, feature_awarded_score, reason,
               reasoning_type, evidence
        FROM claim_compare_results
        WHERE claim_compare_run_id = $1
        ORDER BY product_name ASC, claim_id ASC, feature_id ASC
        LIMIT 120
      `,
      [claimCompareRunId],
    ),
    pgQuery(
      `
        SELECT COUNT(DISTINCT product_id) AS product_count,
               COUNT(*) AS feature_count,
               COUNT(*) FILTER (
                 WHERE COALESCE(reason, '') LIKE '%大模型调用失败%'
                    OR COALESCE(reason, '') LIKE '%模型调用失败%'
                    OR COALESCE(reason, '') LIKE '%规则兜底%'
                    OR COALESCE(evidence, '') LIKE '%大模型调用失败%'
                    OR COALESCE(evidence, '') LIKE '%规则兜底%'
                    OR COALESCE(comparison_result, '') LIKE '%模型调用失败%'
                    OR COALESCE(comparison_result, '') LIKE '%规则兜底%'
               ) AS llm_error_count
        FROM claim_compare_results
        WHERE claim_compare_run_id = $1
      `,
      [claimCompareRunId],
    ),
  ]);
  const counts =
    countResult.rows[0] as
      | { product_count?: string; feature_count?: string; llm_error_count?: string }
      | undefined;
  return {
    rows: rowsResult.rows as JsonObject[],
    productCount: Number(counts?.product_count || 0),
    featureCount: Number(counts?.feature_count || 0),
    llmErrorCount: Number(counts?.llm_error_count || 0),
  };
}

async function runPatentParseStep(
  body: PipelineRequest,
  configs: ReturnType<typeof getModuleConfigs>,
) {
  const startedAt = Date.now();
  const resolvedInput = await resolvePatentInput(body.patentInput);
  const safeSessionId = readText(body.analysisSessionId)
    .replace(/[^a-zA-Z0-9_-]+/g, '_')
    .slice(0, 80);
  const taskId = `product_pipeline_${safeSessionId || Date.now()}_${Date.now()}`;
  const result = await callModule(
    configs.module1,
    {
      patent_file: {
        url: resolvedInput.patentFileUrl,
        file_type: 'image',
      },
      task_id: taskId,
    },
    30 * 60 * 1000,
  );
  const patentRecordId = Number(result.db_record_id || 0);
  const finalOutput = result.final_output && typeof result.final_output === 'object'
    ? result.final_output as JsonObject
    : {};
  const claims = Array.isArray(finalOutput.claims) ? finalOutput.claims : [];
  const figures = Array.isArray(finalOutput.figures) ? finalOutput.figures : [];
  const failed = moduleFailed(result) || !Number.isInteger(patentRecordId) || patentRecordId <= 0;
  return {
    status: failed ? 'failed' : 'completed',
    elapsedMs: Date.now() - startedAt,
    patentRecordId: patentRecordId > 0 ? patentRecordId : undefined,
    taskId: readText(result.task_id) || taskId,
    runId: readText(result.run_id),
    inputType: resolvedInput.inputType,
    inputLabel: resolvedInput.inputLabel,
    claimsCount: claims.length,
    figuresCount: figures.length,
    metadata: finalOutput.metadata && typeof finalOutput.metadata === 'object'
      ? finalOutput.metadata
      : {},
    finalOutput,
    errorMessage: failed
      ? moduleErrorMessage(result) || '模块1没有写入可供后续模块使用的 patent_record_id'
      : '',
    raw: result,
  };
}

async function runKeywordStep(body: PipelineRequest, configs: ReturnType<typeof getModuleConfigs>) {
  const industry = body.industry || 'general';
  const config = configs[module2ConfigKeyForIndustry(industry)];
  const startedAt = Date.now();
  const result = await callModule(
    config,
    {
      patent_record_id: body.patentRecordId,
      analysis_session_id: body.analysisSessionId || '',
    },
    20 * 60 * 1000,
  );
  const keywordRunId = typeof result.keyword_run_id === 'number' ? result.keyword_run_id : undefined;
  const keywords = await loadKeywords({
    patentRecordId: Number(body.patentRecordId),
    analysisSessionId: body.analysisSessionId || '',
    keywordRunId,
  });
  const keywordsCount = Number(result.keywords_count || keywords.length || 0);
  const failed = moduleFailed(result) || keywords.length === 0;
  return {
    status: failed ? 'failed' : 'completed',
    elapsedMs: Date.now() - startedAt,
    industry,
    keywordRunId,
    keywordsCount,
    errorMessage: failed ? moduleErrorMessage(result) || '模块2没有生成关键词' : '',
    keywords,
    raw: result,
  };
}

async function runProductSearchStep(
  body: PipelineRequest,
  configs: ReturnType<typeof getModuleConfigs>,
  generatedKeywords?: string[],
) {
  const startedAt = Date.now();
  const maxKeywords = positiveInteger(body.maxKeywords, 3, 1, 50);
  let inputKeywords = normalizeKeywords(generatedKeywords);
  if (inputKeywords.length === 0) {
    inputKeywords = normalizeKeywords(body.manualKeywords);
  }
  if (inputKeywords.length === 0) {
    const existingKeywords = await loadKeywords({
      patentRecordId: Number(body.patentRecordId),
      analysisSessionId: body.analysisSessionId || '',
    });
    inputKeywords = keywordTexts(existingKeywords, maxKeywords);
  }

  if (inputKeywords.length === 0) {
    return {
      status: 'failed' as StepStatus,
      elapsedMs: Date.now() - startedAt,
      productDetailSearchRunId: undefined,
      acceptedProductsCount: 0,
      totalCandidateLinksCount: 0,
      rejectedCandidatesCount: 0,
      keywords: [],
      products: [],
      candidateSummary: [],
      candidatesPreview: [],
      errorMessage: '没有可用于新模块三的关键词。请先点击“关键词”生成，或选择示例专利/填写手动关键词。',
      raw: {},
    };
  }

  const result = await callModule(
    configs.productDetail,
    {
      patent_record_id: body.patentRecordId,
      analysis_session_id: body.analysisSessionId || '',
      input_keywords: inputKeywords,
      platforms: Array.isArray(body.platforms) && body.platforms.length > 0 ? body.platforms : DEFAULT_PLATFORMS,
      max_keywords: maxKeywords,
      max_candidates_per_keyword: positiveInteger(body.maxCandidatesPerKeyword, 8, 1, 30),
      max_detail_candidates: positiveInteger(body.maxDetailCandidates, 30, 1, 100),
      max_products: positiveInteger(body.maxProducts, 3, 1, 50),
      request_timeout_seconds: positiveInteger(body.requestTimeoutSeconds, 15, 5, 120),
      serp_url_limit: positiveInteger(body.serpUrlLimit, 4, 1, 10),
      persist: true,
    },
    30 * 60 * 1000,
  );
  const productDetailSearchRunId =
    typeof result.product_detail_search_run_id === 'number' ? result.product_detail_search_run_id : undefined;
  const products = await loadProductDetailProducts({
    patentRecordId: Number(body.patentRecordId),
    analysisSessionId: body.analysisSessionId || '',
    productDetailSearchRunId,
  });
  const acceptedProductsCount = Number(result.accepted_products_count || products.length || 0);
  const totalCandidateLinksCount = Number(result.total_candidate_links_count || 0);
  const rejectedCandidatesCount = Number(result.rejected_candidates_count || 0);
  const candidateSummary = await loadCandidateSummary(productDetailSearchRunId);
  const errorMessage = moduleErrorMessage(result);
  const duplicateProductRows = countDuplicateProductRows(products);
  const failed = moduleFailed(result) || acceptedProductsCount === 0 || products.length === 0 || duplicateProductRows > 0;
  return {
    status: failed ? 'failed' : 'completed',
    elapsedMs: Date.now() - startedAt,
    productDetailSearchRunId,
    acceptedProductsCount,
    totalCandidateLinksCount,
    rejectedCandidatesCount,
    keywords: Array.isArray(result.keywords) ? result.keywords : inputKeywords,
    products,
    duplicateProductRows,
    candidateSummary,
    candidatesPreview: Array.isArray(result.candidates_preview) ? result.candidates_preview : [],
    errorMessage: failed
      ? errorMessage ||
        (duplicateProductRows > 0
          ? `新模块三返回 ${duplicateProductRows} 条重复商品`
          : totalCandidateLinksCount === 0
            ? '新模块三没有找到候选详情页'
            : '新模块三没有 accepted 商品')
      : '',
    raw: result,
  };
}

async function runClaimCompareStep(body: PipelineRequest, configs: ReturnType<typeof getModuleConfigs>) {
  const startedAt = Date.now();
  const result = await callModule(
    configs.module4,
    {
      patent_record_id: body.patentRecordId,
      analysis_session_id: body.analysisSessionId || '',
    },
    30 * 60 * 1000,
  );
  const claimCompareRunId = typeof result.claim_compare_run_id === 'number' ? result.claim_compare_run_id : undefined;
  const compareRows = await loadClaimCompareResults(claimCompareRunId);
  const failed =
    moduleFailed(result) ||
    compareRows.productCount === 0 ||
    compareRows.featureCount === 0 ||
    compareRows.llmErrorCount > 0;
  return {
    status: failed ? 'failed' : 'completed',
    elapsedMs: Date.now() - startedAt,
    claimCompareRunId,
    resultSummary: String(result.result_summary || ''),
    productCount: compareRows.productCount,
    featureCount: compareRows.featureCount,
    llmErrorCount: compareRows.llmErrorCount,
    errorMessage: failed
      ? moduleErrorMessage(result) ||
        (compareRows.llmErrorCount > 0
          ? `模块四存在 ${compareRows.llmErrorCount} 行模型调用失败兜底结果`
          : '模块四没有生成有效比对结果')
      : '',
    rows: compareRows.rows,
    raw: result,
  };
}

function collectStepFailure(step: unknown, label: string): string | null {
  if (!step || typeof step !== 'object') return null;
  const payload = step as JsonObject;
  if (payload.status !== 'failed') return null;
  return `${label}: ${readText(payload.errorMessage) || '失败'}`;
}

function normalizePatentNumber(value: unknown): string {
  return readText(value).toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function pickExampleKeywords(rows: JsonObject[], limit: number): string[] {
  const sorted = [...rows].sort((left, right) => {
    const leftType = readText(left.keyword_type).toUpperCase();
    const rightType = readText(right.keyword_type).toUpperCase();
    const priorityDiff = (KEYWORD_PRIORITY[leftType] ?? 50) - (KEYWORD_PRIORITY[rightType] ?? 50);
    if (priorityDiff !== 0) return priorityDiff;
    const confidenceDiff = Number(right.confidence_score || 0) - Number(left.confidence_score || 0);
    if (confidenceDiff !== 0) return confidenceDiff;
    return Number(left.id || 0) - Number(right.id || 0);
  });
  return keywordTexts(sorted, limit);
}

async function loadExampleKeywordRows(record: JsonObject): Promise<JsonObject[]> {
  const recordId = Number(record.id);
  const patentNumber = normalizePatentNumber(record.patent_number);
  const title = readText(record.title);
  const result = await pgQuery(
    `
      SELECT k.id, k.keyword_text, k.keyword_type, k.confidence_score,
             k.analysis_session_id, k.patent_record_id AS keyword_patent_record_id,
             p.patent_number, p.title
      FROM patent_parse_records p
      JOIN keyword_records k ON k.patent_record_id = p.id
      WHERE k.keyword_text IS NOT NULL
        AND trim(k.keyword_text) <> ''
        AND (
          p.id = $1
          OR regexp_replace(upper(coalesce(p.patent_number, '')), '[^A-Z0-9]', '', 'g') = $2
          OR ($3 <> '' AND trim(coalesce(p.title, '')) = $3)
        )
      ORDER BY k.id ASC
      LIMIT 200
    `,
    [recordId, patentNumber, title],
  );
  return result.rows as JsonObject[];
}

async function loadLatestProductRun(recordId: number): Promise<JsonObject | null> {
  const result = await pgQuery(
    `
      SELECT r.id, r.status, r.analysis_session_id, r.accepted_products_count,
             r.rejected_candidates_count, r.error_message, r.finished_at,
             COUNT(p.id)::int AS product_rows,
             COALESCE(SUM(jsonb_array_length(coalesce(p.picture, jsonb_build_array()))), 0)::int AS image_count
      FROM product_detail_search_runs r
      LEFT JOIN product_detail_search_products p ON p.run_id = r.id
      WHERE r.patent_record_id = $1
      GROUP BY r.id
      ORDER BY r.created_at DESC, r.id DESC
      LIMIT 1
    `,
    [recordId],
  );
  return (result.rows[0] as JsonObject | undefined) || null;
}

async function loadLatestClaimRun(recordId: number): Promise<JsonObject | null> {
  const result = await pgQuery(
    `
      SELECT id, analysis_session_id, status, product_count, result_summary,
             error_message, finished_at
      FROM claim_compare_runs
      WHERE patent_record_id = $1
      ORDER BY created_at DESC, id DESC
      LIMIT 1
    `,
    [recordId],
  );
  return (result.rows[0] as JsonObject | undefined) || null;
}

async function loadExamplePatents(): Promise<JsonObject[]> {
  const result = await pgQuery(
    `
      SELECT id, task_id, patent_number, title
      FROM patent_parse_records
      WHERE task_id LIKE $1
      ORDER BY task_id ASC, id ASC
    `,
    [`${EXAMPLE_TASK_PREFIX}%`],
  );
  const examples: JsonObject[] = [];
  for (const record of result.rows as JsonObject[]) {
    const recordId = Number(record.id);
    const keywordRows = await loadExampleKeywordRows(record);
    const keywords = pickExampleKeywords(keywordRows, 3);
    const firstKeyword = keywordRows[0];
    const [latestProductRun, latestClaimRun] = await Promise.all([
      loadLatestProductRun(recordId),
      loadLatestClaimRun(recordId),
    ]);
    examples.push({
      id: recordId,
      taskId: record.task_id,
      patentNumber: record.patent_number,
      title: record.title,
      keywords,
      keywordCount: keywordRows.length,
      keywordSourcePatentRecordId: firstKeyword?.keyword_patent_record_id || null,
      keywordSourceSessionId: firstKeyword?.analysis_session_id || null,
      latestProductRun,
      latestClaimRun,
    });
  }
  return examples;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  try {
    const examples = await loadExamplePatents();
    return NextResponse.json({ ok: true, examples });
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : String(error) },
      { status: 500 },
    );
  }
}

async function runCanonicalPatentAnalysisModules(body: PipelineRequest): Promise<JsonObject> {
  const startedAt = Date.now();
  const action: PipelineAction = body.action || 'all';
  const analysisSessionId = String(body.analysisSessionId || '').trim() || `module_test_${Date.now()}`;
  const patentRecordId = Number(body.patentRecordId);
  if (action !== 'patentParse' && (!Number.isInteger(patentRecordId) || patentRecordId <= 0)) {
    throw new Error('patentRecordId 必填且必须为正整数');
  }
  const normalizedBody: PipelineRequest = {
    ...body,
    action,
    patentRecordId,
    analysisSessionId,
    industry: body.industry || 'general',
  };
  const configs = getModuleConfigs();
  const response: JsonObject = {
    ok: true,
    action,
    patentRecordId,
    analysisSessionId,
    startedAt: new Date(startedAt).toISOString(),
  };

  if (action === 'patentParse') {
    const patentParseStep = await runPatentParseStep(normalizedBody, configs);
    response.patentParseStep = patentParseStep;
    response.patentRecordId = patentParseStep.patentRecordId;
    const failure = collectStepFailure(patentParseStep, '模块1专利解析');
    if (failure) {
      response.ok = false;
      response.error = failure;
    }
    response.finishedAt = new Date().toISOString();
    response.elapsedMs = Date.now() - startedAt;
    return response;
  }

  let generatedKeywords: string[] = [];
  if (action === 'keywords' || action === 'all') {
    const keywordStep = await runKeywordStep(normalizedBody, configs);
    response.keywordStep = keywordStep;
    generatedKeywords = keywordTexts(Array.isArray(keywordStep.keywords) ? keywordStep.keywords : [], 50);
  } else {
    response.keywords = await loadKeywords({ patentRecordId, analysisSessionId });
  }

  if (action === 'productSearch' || action === 'all') {
    const keywordFailed = collectStepFailure(response.keywordStep, '模块2关键词');
    if (action === 'all' && keywordFailed) {
      response.productSearchStep = {
        status: 'skipped',
        elapsedMs: 0,
        products: [],
        errorMessage: '模块2没有生成关键词，已跳过新模块三。',
      };
    } else {
      response.productSearchStep = await runProductSearchStep(normalizedBody, configs, generatedKeywords);
    }
  }

  if (action === 'claimCompare' || action === 'all') {
    const productStep = response.productSearchStep as JsonObject | undefined;
    if (action === 'all' && productStep?.status !== 'completed') {
      response.claimCompareStep = {
        status: 'skipped',
        elapsedMs: 0,
        rows: [],
        productCount: 0,
        featureCount: 0,
        errorMessage: '新模块三没有 accepted 商品，已跳过模块四。',
      };
    } else {
      response.claimCompareStep = await runClaimCompareStep(normalizedBody, configs);
    }
  }

  const failures = [
    collectStepFailure(response.keywordStep, '模块2关键词'),
    collectStepFailure(response.productSearchStep, '新模块三'),
    collectStepFailure(response.claimCompareStep, '模块四'),
  ].filter(Boolean);
  if (failures.length > 0) {
    response.ok = false;
    response.error = failures.join('；');
  }

  response.finishedAt = new Date().toISOString();
  response.elapsedMs = Date.now() - startedAt;
  return response;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  const startedAt = Date.now();
  try {
    const body = (await request.json()) as PipelineRequest;
    return NextResponse.json(await runCanonicalPatentAnalysisModules(body));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      {
        ok: false,
        error: message,
        elapsedMs: Date.now() - startedAt,
      },
      { status: message.includes('patentRecordId 必填') ? 400 : 500 },
    );
  }
}
