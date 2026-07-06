import { NextRequest, NextResponse } from 'next/server';
import { module2ConfigKeyForIndustry } from '@/lib/industry-keyword-flow';
import { pgQuery } from '@/lib/postgres';
import type { IndustryType } from '@/lib/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';
export const maxDuration = 1800;

type PipelineAction = 'keywords' | 'productSearch' | 'claimCompare' | 'all';

type PipelineRequest = {
  action?: PipelineAction;
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

function getWorkflowBaseUrl(port: number): string {
  return `http://127.0.0.1:${port}/run`;
}

function getModuleConfigs(): Record<'module2' | 'module2Fitness' | 'module2HomeAppliances' | 'productDetail' | 'module4', ModuleConfig> {
  return {
    module2: {
      url: process.env.MODULE2_API_URL || getWorkflowBaseUrl(5102),
      token: process.env.MODULE2_API_TOKEN || undefined,
    },
    module2Fitness: {
      url: process.env.MODULE2_FITNESS_API_URL || getWorkflowBaseUrl(5103),
      token: process.env.MODULE2_FITNESS_API_TOKEN || undefined,
    },
    module2HomeAppliances: {
      url: process.env.MODULE2_HOME_APPLIANCES_API_URL || getWorkflowBaseUrl(5104),
      token: process.env.MODULE2_HOME_APPLIANCES_API_TOKEN || undefined,
    },
    productDetail: {
      url:
        process.env.PRODUCT_DETAIL_MODULE3_API_URL ||
        process.env.PRODUCT_SEARCH_API_URL ||
        'http://127.0.0.1:5107/run',
      token: process.env.PRODUCT_DETAIL_MODULE3_API_TOKEN || undefined,
    },
    module4: {
      url: process.env.MODULE4_API_URL || getWorkflowBaseUrl(5106),
      token: process.env.MODULE4_API_TOKEN || undefined,
    },
  };
}

function createHeaders(token?: string): HeadersInit {
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

async function callModule(config: ModuleConfig, payload: JsonObject, timeoutMs: number): Promise<JsonObject> {
  const response = await fetch(config.url, {
    method: 'POST',
    headers: createHeaders(config.token),
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  let data: JsonObject;
  try {
    data = JSON.parse(text) as JsonObject;
  } catch {
    data = { raw: text };
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
}> {
  if (!claimCompareRunId) {
    return { rows: [], productCount: 0, featureCount: 0 };
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
               COUNT(*) AS feature_count
        FROM claim_compare_results
        WHERE claim_compare_run_id = $1
      `,
      [claimCompareRunId],
    ),
  ]);
  const counts = countResult.rows[0] as { product_count?: string; feature_count?: string } | undefined;
  return {
    rows: rowsResult.rows as JsonObject[],
    productCount: Number(counts?.product_count || 0),
    featureCount: Number(counts?.feature_count || 0),
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
  return {
    status: 'completed',
    elapsedMs: Date.now() - startedAt,
    industry,
    keywordRunId,
    keywordsCount: Number(result.keywords_count || keywords.length || 0),
    keywords,
    raw: result,
  };
}

async function runProductSearchStep(body: PipelineRequest, configs: ReturnType<typeof getModuleConfigs>) {
  const manualKeywords = normalizeKeywords(body.manualKeywords);
  const startedAt = Date.now();
  const result = await callModule(
    configs.productDetail,
    {
      patent_record_id: body.patentRecordId,
      analysis_session_id: body.analysisSessionId || '',
      input_keywords: manualKeywords.length > 0 ? manualKeywords : null,
      platforms: Array.isArray(body.platforms) && body.platforms.length > 0 ? body.platforms : DEFAULT_PLATFORMS,
      max_keywords: positiveInteger(body.maxKeywords, 3, 1, 50),
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
  return {
    status: 'completed',
    elapsedMs: Date.now() - startedAt,
    productDetailSearchRunId,
    acceptedProductsCount: Number(result.accepted_products_count || products.length || 0),
    totalCandidateLinksCount: Number(result.total_candidate_links_count || 0),
    rejectedCandidatesCount: Number(result.rejected_candidates_count || 0),
    keywords: Array.isArray(result.keywords) ? result.keywords : [],
    products,
    candidatesPreview: Array.isArray(result.candidates_preview) ? result.candidates_preview : [],
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
  return {
    status: 'completed',
    elapsedMs: Date.now() - startedAt,
    claimCompareRunId,
    resultSummary: String(result.result_summary || ''),
    productCount: compareRows.productCount,
    featureCount: compareRows.featureCount,
    rows: compareRows.rows,
    raw: result,
  };
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const startedAt = Date.now();
  try {
    const body = (await request.json()) as PipelineRequest;
    const patentRecordId = Number(body.patentRecordId);
    if (!Number.isInteger(patentRecordId) || patentRecordId <= 0) {
      return NextResponse.json({ error: 'patentRecordId 必填且必须为正整数' }, { status: 400 });
    }
    const action: PipelineAction = body.action || 'all';
    const analysisSessionId = String(body.analysisSessionId || '').trim() || `module_test_${Date.now()}`;
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

    if (action === 'keywords' || action === 'all') {
      response.keywordStep = await runKeywordStep(normalizedBody, configs);
    } else {
      response.keywords = await loadKeywords({ patentRecordId, analysisSessionId });
    }

    if (action === 'productSearch' || action === 'all') {
      response.productSearchStep = await runProductSearchStep(normalizedBody, configs);
    }

    if (action === 'claimCompare' || action === 'all') {
      response.claimCompareStep = await runClaimCompareStep(normalizedBody, configs);
    }

    response.finishedAt = new Date().toISOString();
    response.elapsedMs = Date.now() - startedAt;
    return NextResponse.json(response);
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        elapsedMs: Date.now() - startedAt,
      },
      { status: 500 },
    );
  }
}
