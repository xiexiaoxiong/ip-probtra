// ============================================================
// 专利分析编排 API
// 架构：POST 触发后台执行 → 前端轮询 /api/analysis/[id] 获取进度
//
// 严格遵循单向依赖：模块1 → 模块2 → 模块3 → 模块4 → 提取结果
// 每个模块仅使用上游输出，不反向依赖
// 模块间通过飞书多维表格 (feishu_url) 传递数据
// 通过扣子编程项目的自定义域名 /run 端点调用
// ============================================================

import { mkdir, writeFile } from 'fs/promises';
import path from 'path';
import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import {
  runModule1,
  runModule2,
  runModule3,
  runModule4,
  detectIndustry,
  warmupCozeSearch,
} from '@/lib/workflow-client';
import type { Module1Result, Module2Result, Module3Result, Module4Result } from '@/lib/workflow-client';
import type { IndustryType } from '@/lib/types';
import {
  createSession,
  updateSessionStatus,
  updateStepStatus,
  updateResults,
  getSessionAsync,
} from '@/lib/analysis-store';
import type {
  KeywordConfirmationState,
  PatentInfo,
  ProductComparison,
  ProductInfo,
} from '@/lib/types';
import { normalizeKeywordList } from '@/lib/keyword-utils';
import { getUploadsDir } from '@/lib/runtime-paths';
import { pgQuery } from '@/lib/postgres';
import { createErrorReport } from '@/lib/error-reports-store';

// ============================================================
// 数据映射工具函数
// ============================================================

/** 从模块4 API 响应的 all_comparison_results 提取比对数据
 *  支持两种数据格式：
 *  1. 嵌套格式（模块4标准输出）：每项是商品对象，包含 features 数组
 *     [{ product_id, product_name, features: [{ feature_id, feature_text, evidence, comparison_result, reason, reasoning_type, claim_id }] }]
 *  2. 扁平格式（兼容旧版/飞书格式）：每项是单条比对记录
 */
function mapComparisonsFromApi(rawResults: unknown[]): ProductComparison[] {
  if (!Array.isArray(rawResults) || rawResults.length === 0) return [];

  const productMap = new Map<string, {
    productId: string;
    productName: string;
    elements: ProductComparison['claimElements'];
  }>();

  for (const item of rawResults) {
    if (!item || typeof item !== 'object') continue;

    const record = item as Record<string, unknown>;

    const productId = getStringField(record, 'product_id', '商品ID', 'productId');
    const productName = getStringField(record, 'product_name', '商品名称', 'productName', '产品名称');

    // 检测嵌套格式：商品对象包含 features 数组
    const features = record['features'];
    if (Array.isArray(features) && features.length > 0) {
      const id = productId || productName || `product_${productMap.size + 1}`;
      if (!productMap.has(id)) {
        productMap.set(id, { productId: id, productName: productName || id, elements: [] });
      }

      for (const feat of features) {
        if (!feat || typeof feat !== 'object') continue;
        const f = feat as Record<string, unknown>;

        const featureText = getStringField(f, 'feature_text', '权利要求特征', '专利技术特征', 'featureElement', 'claimElement', 'claim_element');
        const evidence = getStringField(f, 'evidence', '商品特征', '产品技术特征', 'productFeature', 'product_feature', 'product_feature_evidence');
        const statusRaw = getStringField(f, 'comparison_result', 'status', '匹配状态', '比对结果', 'matchStatus', 'result');
        const reason = getStringField(f, 'reason', '推理过程', '比对分析', '分析', 'reasoning');
        const reasoningType = getStringField(f, 'reasoning_type', '推理类型');
        const featureId = getStringField(f, 'feature_id', '特征编号');
        
        // 提取 evidence_images
        const evidenceImagesRaw = f['evidence_images'];
        const evidenceImages: string[] = Array.isArray(evidenceImagesRaw) 
          ? evidenceImagesRaw.filter((u): u is string => typeof u === 'string' && u.trim().length > 0)
          : [];

        // feature_text 作为权利要求特征描述，evidence 作为商品侧证据
        productMap.get(id)!.elements.push({
          featureId: featureId || undefined,
          claimElement: featureText || '',
          productFeature: evidence || '',
          status: normalizeStatus(statusRaw),
          reasoning: [reason, reasoningType].filter(Boolean).join(' | ') || '',
          patentReference: getStringField(f, 'claim_id') || undefined,
          evidenceImages: evidenceImages.length > 0 ? evidenceImages : undefined,
        });
      }
      continue;
    }

    // 扁平格式：每条记录就是单条比对
    const claimElement = getStringField(record, 'claim_element', '权利要求特征', '专利技术特征', 'claimElement', 'feature_text');
    const productFeature = getStringField(record, 'product_feature', '商品特征', '产品技术特征', 'productFeature', 'evidence');
    const status = getStringField(record, 'status', '匹配状态', '比对结果', 'matchStatus', 'comparison_result');
    const reasoning = getStringField(record, 'reasoning', '推理过程', '比对分析', '分析', 'reason');

    if (!claimElement && !productFeature) {
      if (productId || productName) {
        const id = productId || productName || `product_${productMap.size + 1}`;
        if (!productMap.has(id)) {
          productMap.set(id, { productId: id, productName: productName || id, elements: [] });
        }
      }
      continue;
    }

    const id = productId || `product_${productMap.size + 1}`;
    if (!productMap.has(id)) {
      productMap.set(id, { productId: id, productName: productName || id, elements: [] });
    }

    productMap.get(id)!.elements.push({
      claimElement: claimElement || '',
      productFeature: productFeature || '',
      status: normalizeStatus(status),
      reasoning: reasoning || '',
    });
  }

  return Array.from(productMap.values()).map(group => ({
    productId: group.productId,
    productName: group.productName,
    overallVerdict: determineVerdict(group.elements),
    claimElements: group.elements,
  }));
}

function getStringField(record: Record<string, unknown>, ...candidates: string[]): string {
  for (const key of candidates) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (Array.isArray(value)) {
      const text = value
        .map((item: unknown) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'text' in item) return (item as { text: string }).text;
          return '';
        })
        .join('')
        .trim();
      if (text) return text;
    }
  }
  return '';
}

function normalizeStatus(status: string): ProductComparison['claimElements'][0]['status'] {
  if (!status) return 'uncertain';
  const lower = status.trim().toLowerCase();
  const compact = lower.replace(/[\s_-]+/g, '');

  if (
    lower.includes('不匹配')
    || lower.includes('不相同')
    || lower.includes('不同')
    || lower.includes('不一致')
    || lower.includes('区别')
    || lower.includes('no_match')
    || lower.includes('no-match')
    || lower.includes('no match')
    || lower.includes('not_match')
    || lower.includes('not-match')
    || lower.includes('not match')
    || compact.includes('nomatch')
    || compact.includes('notmatch')
  ) {
    return 'not_matching';
  }

  if (
    lower.includes('匹配')
    || lower.includes('matching')
    || /\bmatch\b/.test(lower)
    || lower.includes('相同')
    || lower.includes('一致')
    || lower.includes('等同')
  ) {
    return 'matching';
  }

  return 'uncertain';
}

function determineVerdict(elements: ProductComparison['claimElements']): ProductComparison['overallVerdict'] {
  if (elements.length === 0) return 'uncertain';
  const matching = elements.filter(e => e.status === 'matching').length;
  const notMatching = elements.filter(e => e.status === 'not_matching').length;
  if (notMatching === 0 && matching === elements.length) return 'infringement_likely';
  if (notMatching > 0 && matching === 0) return 'no_infringement';
  return 'uncertain';
}

/** 将数据库扁平行按 product_name/product_id 分组为嵌套格式（同模块4输出结构） */
function groupDbRowsByProduct(rows: unknown[]): unknown[] {
  const productMap = new Map<string, { product_id: string; product_name: string; features: unknown[] }>();

  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;

    const rawProductId = r['product_id'];
    const rawProductName = r['product_name'];
    const productId = typeof rawProductId === 'string' && rawProductId.trim() ? rawProductId.trim()
      : typeof rawProductId === 'number' ? String(rawProductId)
      : '';
    const productName = typeof rawProductName === 'string' && rawProductName.trim() ? rawProductName.trim() : '';

    const key = productId || productName || `product_${productMap.size + 1}`;
    if (!productMap.has(key)) {
      productMap.set(key, { product_id: productId || key, product_name: productName || key, features: [] });
    }

    const feature: Record<string, unknown> = {};
    if (r['feature_id'] != null) feature['feature_id'] = r['feature_id'];
    if (r['feature_text'] != null) feature['feature_text'] = r['feature_text'];
    if (r['claim_id'] != null) feature['claim_id'] = r['claim_id'];
    if (r['evidence'] != null) feature['evidence'] = r['evidence'];
    if (r['comparison_result'] != null) feature['comparison_result'] = r['comparison_result'];
    if (r['reason'] != null) feature['reason'] = r['reason'];
    if (r['reasoning_type'] != null) feature['reasoning_type'] = r['reasoning_type'];
    if (r['evidence_images'] != null) feature['evidence_images'] = r['evidence_images'];
    if (r['raw_payload'] != null && typeof r['raw_payload'] === 'object') {
      const payload = r['raw_payload'] as Record<string, unknown>;
      for (const k of ['feature_id', 'feature_text', 'claim_id', 'evidence', 'comparison_result', 'reason', 'reasoning_type', 'evidence_images']) {
        if (payload[k] != null && !(k in feature)) feature[k] = payload[k];
      }
    }

    productMap.get(key)!.features.push(feature);
  }

  return Array.from(productMap.values());
}

function mapPatentFromModule1(module1Result: Module1Result): PatentInfo | undefined {
  const finalOutput = module1Result.finalOutput;
  if (!finalOutput) return undefined;

  const claims = Array.isArray(finalOutput.claims) ? finalOutput.claims : [];
  const independentClaims = claims
    .filter((claim) => claim.claim_type === 'INDEPENDENT' && claim.claim_text)
    .map((claim) => String(claim.claim_text));
  const dependentClaims = claims
    .filter((claim) => claim.claim_type === 'DEPENDENT' && claim.claim_text)
    .map((claim) => String(claim.claim_text));

  const specificationMap = finalOutput.specification && typeof finalOutput.specification === 'object'
    ? finalOutput.specification
    : {};
  const specification = Object.entries(specificationMap)
    .map(([section, text]) => `${section}\n${text}`)
    .join('\n\n')
    .trim();

  const drawings = Array.isArray(finalOutput.figures)
    ? finalOutput.figures
        .map((figure) => figure.figure_url)
        .filter((url): url is string => typeof url === 'string' && url.trim().length > 0)
    : [];

  return {
    title: finalOutput.metadata?.title,
    patentNumber: finalOutput.metadata?.patent_number,
    independentClaims: independentClaims.length > 0 ? independentClaims : undefined,
    dependentClaims: dependentClaims.length > 0 ? dependentClaims : undefined,
    specification: specification || undefined,
    drawings: drawings.length > 0 ? drawings : undefined,
  };
}

function explainModule1LocalBlockers(module1Result: Module1Result): string {
  const errorMessages = (module1Result.finalOutput?.errors || [])
    .map((error) => error.error_message)
    .filter((message): message is string => typeof message === 'string' && message.trim().length > 0);

  const knownHints: string[] = [
    '部分节点仍依赖 Coze/OpenAI 兼容模型网关',
    '第三阶段当前默认使用 Bright Data + Baidu 的中文检索通道',
    '请确认 `PGDATABASE_URL` 已正确配置，模块1需要先写入本地数据库',
  ];
  const joined = errorMessages.join(' | ');

  if (joined.includes('COZE_WORKLOAD_IDENTITY_API_KEY')) {
    knownHints.push('缺少大模型配置 `COZE_WORKLOAD_IDENTITY_API_KEY` / `COZE_INTEGRATION_MODEL_BASE_URL`');
  }
  if (joined.includes('PGDATABASE_URL')) {
    knownHints.push('缺少数据库配置 `PGDATABASE_URL`');
  }
  if (joined.includes('BRIGHTDATA_API_KEY')) {
    knownHints.push('缺少搜索配置 `BRIGHTDATA_API_KEY`');
  }
  if (joined.includes('Feishu') || joined.includes('飞书') || joined.includes('FEISHU_APP_ID')) {
    knownHints.push('缺少飞书凭据或尚未替换飞书表格通道');
  }

  const details = [
    '模块1已返回结构化解析结果，但当前整条链路仍未完成本地化。',
    knownHints.length > 0 ? `阻塞项: ${knownHints.join('；')}` : '',
    errorMessages.length > 0 ? `模块1错误详情: ${errorMessages.join('；')}` : '',
  ].filter(Boolean);

  return details.join(' ');
}

async function persistTextInput(sessionId: string, text: string): Promise<string> {
  const uploadsDir = getUploadsDir();
  await mkdir(uploadsDir, { recursive: true });

  const filePath = path.join(uploadsDir, `${sessionId}.txt`);
  await writeFile(filePath, text, 'utf-8');
  return filePath;
}

function isRecoverableWorkflowTransportError(message: string): boolean {
  const normalized = message.toLowerCase();
  return (
    normalized.includes('fetch failed')
    || normalized.includes('504')
    || normalized.includes('超时')
    || normalized.includes('timeout')
    || normalized.includes('socket')
    || normalized.includes('network')
    || normalized.includes('aborted')
  );
}

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

interface SearchRunSnapshot {
  id: number;
  total_products_count: number;
  is_complete: boolean;
  error_message: string | null;
}

interface ClaimCompareRunSnapshot {
  id: number;
  product_count: number;
  result_summary: string | null;
}

async function getLatestSearchRunSnapshot(
  sessionId: string,
  patentRecordId: number,
): Promise<SearchRunSnapshot | null> {
  const result = await pgQuery<SearchRunSnapshot>(
    `
      select id, total_products_count, is_complete, error_message
      from search_runs
      where analysis_session_id = $1 and patent_record_id = $2
      order by id desc
      limit 1
    `,
    [sessionId, patentRecordId],
  );
  return result.rows[0] || null;
}

async function getLatestClaimCompareRunSnapshot(
  sessionId: string,
  patentRecordId: number,
): Promise<ClaimCompareRunSnapshot | null> {
  const result = await pgQuery<ClaimCompareRunSnapshot>(
    `
      select id, product_count, result_summary
      from claim_compare_runs
      where analysis_session_id = $1 and patent_record_id = $2
      order by id desc
      limit 1
    `,
    [sessionId, patentRecordId],
  );
  return result.rows[0] || null;
}

async function waitForSearchRunRecovery(
  sessionId: string,
  patentRecordId: number,
  startedAt: number,
  maxWaitFromStartMs: number = 12 * 60 * 1000,
  intervalMs: number = 10 * 1000,
): Promise<SearchRunSnapshot | null> {
  while (Date.now() - startedAt < maxWaitFromStartMs) {
    try {
      const snapshot = await getLatestSearchRunSnapshot(sessionId, patentRecordId);
      if (snapshot) {
        return snapshot;
      }
    } catch (error) {
      console.warn(`[Pipeline ${sessionId}] 查询 search_runs 恢复状态失败:`, error);
    }
    await sleep(intervalMs);
  }
  return null;
}

async function waitForClaimCompareRunRecovery(
  sessionId: string,
  patentRecordId: number,
  startedAt: number,
  maxWaitFromStartMs: number = 15 * 60 * 1000,
  intervalMs: number = 10 * 1000,
): Promise<ClaimCompareRunSnapshot | null> {
  while (Date.now() - startedAt < maxWaitFromStartMs) {
    try {
      const snapshot = await getLatestClaimCompareRunSnapshot(sessionId, patentRecordId);
      if (snapshot) {
        return snapshot;
      }
    } catch (error) {
      console.warn(`[Pipeline ${sessionId}] 查询 claim_compare_runs 恢复状态失败:`, error);
    }
    await sleep(intervalMs);
  }
  return null;
}

async function getPatentClaimsCount(patentRecordId: number): Promise<number | null> {
  try {
    const exists = await pgQuery<{ exists: string | null }>(
      `select to_regclass('patent_claims') as exists`,
    );
    if (!exists.rows[0]?.exists) {
      return null;
    }
    const result = await pgQuery<{ count: string }>(
      `select count(*)::text as count from patent_claims where record_id = $1`,
      [patentRecordId],
    );
    const raw = result.rows[0]?.count;
    return raw ? Number(raw) : 0;
  } catch {
    return null;
  }
}

async function getPatentFigureUrls(patentRecordId: number): Promise<string[] | null> {
  try {
    const exists = await pgQuery<{ exists: string | null }>(
      `select to_regclass('patent_figures') as exists`,
    );
    if (!exists.rows[0]?.exists) {
      return null;
    }
    const result = await pgQuery<{ figure_url: string | null }>(
      `select figure_url from patent_figures where record_id = $1 order by id asc`,
      [patentRecordId],
    );
    return result.rows
      .map((row) => row.figure_url)
      .filter((value): value is string => typeof value === 'string' && value.trim().length > 0);
  } catch {
    return null;
  }
}

async function getKeywordTexts(patentRecordId: number, limit: number = 30): Promise<string[] | null> {
  try {
    const exists = await pgQuery<{ exists: string | null }>(
      `select to_regclass('keyword_records') as exists`,
    );
    if (!exists.rows[0]?.exists) {
      return null;
    }
    const result = await pgQuery<{ keyword_text: string | null }>(
      `select keyword_text
       from keyword_records
       where patent_record_id = $1
       order by id asc
       limit $2`,
      [patentRecordId, limit],
    );
    const keywords = result.rows
      .map((r) => (r.keyword_text ? String(r.keyword_text).trim() : ''))
      .filter((k) => k.length > 0);
    return Array.from(new Set(keywords));
  } catch {
    return null;
  }
}

const KEYWORD_CONFIRMATION_TIMEOUT_MS = 30_000;
const KEYWORD_CONFIRMATION_POLL_MS = 1_000;

async function waitForKeywordConfirmation(
  sessionId: string,
  autoKeywords: string[],
): Promise<KeywordConfirmationState> {
  const normalizedAutoKeywords = normalizeKeywordList(autoKeywords);

  while (true) {
    const session = await getSessionAsync(sessionId);
    const state = session?.results?.keywordConfirmation;

    if (!state) {
      const fallbackState: KeywordConfirmationState = {
        status: 'auto_confirmed',
        autoKeywords: normalizedAutoKeywords,
        userKeywords: [],
        finalKeywords: normalizedAutoKeywords,
        confirmedAt: Date.now(),
      };
      await updateResults(sessionId, {
        keywords: fallbackState.finalKeywords,
        keywordConfirmation: fallbackState,
      });
      return fallbackState;
    }

    if (state.status === 'confirmed') {
      return {
        ...state,
        autoKeywords: normalizeKeywordList(state.autoKeywords),
        userKeywords: normalizeKeywordList(state.userKeywords),
        finalKeywords: normalizeKeywordList(state.finalKeywords),
      };
    }

    if (state.status === 'auto_confirmed') {
      return {
        ...state,
        autoKeywords: normalizeKeywordList(state.autoKeywords),
        userKeywords: normalizeKeywordList(state.userKeywords),
        finalKeywords: normalizeKeywordList(state.finalKeywords),
      };
    }

    if (state.status === 'timed_wait') {
      const deadlineAt = state.deadlineAt ?? Date.now() + KEYWORD_CONFIRMATION_TIMEOUT_MS;
      if (Date.now() >= deadlineAt) {
        const autoConfirmedState: KeywordConfirmationState = {
          status: 'auto_confirmed',
          autoKeywords: normalizeKeywordList(state.autoKeywords),
          userKeywords: [],
          finalKeywords: normalizeKeywordList(state.autoKeywords),
          promptedAt: state.promptedAt,
          deadlineAt,
          confirmedAt: Date.now(),
        };
        await updateResults(sessionId, {
          keywords: autoConfirmedState.finalKeywords,
          keywordConfirmation: autoConfirmedState,
        });
        return autoConfirmedState;
      }
    }

    await sleep(KEYWORD_CONFIRMATION_POLL_MS);
  }
}

// ============================================================
// 后台执行分析流水线
// ============================================================

function buildPatentTextSnippet(
  params: { text?: string } | null,
  patent: PatentInfo | null,
): string | null {
  if (params?.text && params.text.trim()) {
    return params.text;
  }
  if (!patent) {
    return null;
  }
  const parts = [
    patent.title,
    patent.patentNumber,
    patent.specification,
    ...(patent.independentClaims || []),
    ...(patent.dependentClaims || []),
  ]
    .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
    .join('\n\n');
  return parts.trim() ? parts : null;
}

async function reportPipelineFailure(input: {
  sessionId: string;
  error: unknown;
  patentText?: string | null;
  inputType?: string | null;
  inputValue?: string | null;
  fileUrl?: string | null;
  meta?: Record<string, unknown>;
}): Promise<void> {
  try {
    const session = await getSessionAsync(input.sessionId);
    const firstErrorStep = session?.steps?.find((step) => step.status === 'error');
    const lastRunningStep = [...(session?.steps || [])].reverse().find((step) => step.status === 'running');
    const step = firstErrorStep || lastRunningStep || null;
    const errorMessage = input.error instanceof Error ? input.error.message : String(input.error);
    const errorStack = input.error instanceof Error ? input.error.stack || null : null;

    await createErrorReport({
      analysisSessionId: input.sessionId,
      userId: session?.userId ?? null,
      stepId: step?.id ?? null,
      stepName: step?.name ?? null,
      errorMessage,
      errorStack,
      patentText: input.patentText ?? null,
      inputType: input.inputType ?? session?.input.type ?? null,
      inputValue: input.inputValue ?? session?.input.value ?? null,
      fileUrl: input.fileUrl ?? session?.input.fileUrl ?? null,
      meta: input.meta ?? {},
    });
  } catch {}
}

async function executePipeline(
  sessionId: string,
  type: 'url' | 'file' | 'text',
  params: { url?: string; fileKey?: string; fileName?: string; fileUrl?: string; text?: string },
): Promise<void> {
  const pipelineStart = Date.now();
  const stepTimings: Record<string, number> = {};
  let patentTextForReport: string | null = type === 'text' ? params.text ?? null : null;
  const inputValueForReport =
    type === 'url' ? params.url ?? null : type === 'file' ? params.fileKey ?? null : params.text ?? null;
  const fileUrlForReport = type === 'file' ? params.fileUrl ?? null : null;

  try {
    await updateSessionStatus(sessionId, 'running');

    // ========== 在流水线开始前预热Coze工作流（防止冷启动） ==========
    warmupCozeSearch().then((result) => {
      console.log(`[Pipeline ${sessionId}] Coze搜索预热${result.ok ? '成功' : '跳过'} (${result.ms}ms${result.error ? ', ' + result.error : ''})`);
    }).catch(() => {});

    // ========== 确定传给模块1的专利文件 URL ==========
    let patentFileUrl: string;
    let patentFileType: string;

    if (type === 'url' && params.url) {
      patentFileUrl = params.url;
      patentFileType = 'image';
    } else if (type === 'file' && params.fileUrl) {
      patentFileUrl = params.fileUrl;
      patentFileType = 'image';
    } else if (type === 'text' && params.text) {
      console.log(`[Pipeline ${sessionId}] 保存专利文本到本地文件...`);
      const uploadStart = Date.now();
      patentFileUrl = await persistTextInput(sessionId, params.text);
      patentFileType = 'image';

      stepTimings['upload'] = Date.now() - uploadStart;
      console.log(`[Pipeline ${sessionId}] 本地文件保存完成 (${stepTimings['upload']}ms)`);
    } else {
      throw new Error('无法获取专利文件：缺少 URL、文件或文本内容');
    }

    // ========== 模块1（步骤1）：专利文本解析 ==========
    await updateStepStatus(sessionId, 1, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤1: 专利文本解析...`);
    const step1Start = Date.now();

    const module1Result: Module1Result = await runModule1(
      patentFileUrl,
      patentFileType,
      (msg) => console.log(`[Pipeline ${sessionId}] 模块1进度: ${msg}`),
    );

    const patentFromModule1 = mapPatentFromModule1(module1Result);
    if (
      patentFromModule1
      && (!patentFromModule1.drawings || patentFromModule1.drawings.length === 0)
      && module1Result.dbRecordId
      && module1Result.dbRecordId > 0
    ) {
      const figureUrls = await getPatentFigureUrls(module1Result.dbRecordId);
      if (figureUrls && figureUrls.length > 0) {
        patentFromModule1.drawings = figureUrls;
      }
    }
    let module1ClaimCount = module1Result.finalOutput?.claims?.length ?? 0;
    if (module1ClaimCount === 0 && module1Result.dbRecordId && module1Result.dbRecordId > 0) {
      const dbCount = await getPatentClaimsCount(module1Result.dbRecordId);
      if (typeof dbCount === 'number' && dbCount > 0) {
        module1ClaimCount = dbCount;
      }
    }

    stepTimings['step1'] = Date.now() - step1Start;
    if (type !== 'text' && module1ClaimCount === 0) {
      const msg =
        '模块1未提取到任何权利要求（数据库中也未写入权利要求记录），通常是扫描版PDF（无文字层）导致。请先对PDF做OCR（导出“可搜索PDF”）后再上传，或确保本机安装 tesseract 后重试。';
      await updateStepStatus(sessionId, 1, 'error', msg);
      await updateSessionStatus(sessionId, 'error');
      await updateResults(sessionId, {
        module1RunId: module1Result.runId,
        dbRecordId: module1Result.dbRecordId,
      });
      throw new Error(msg);
    }

    await updateStepStatus(sessionId, 1, 'completed');
    await updateResults(sessionId, {
      patent: patentFromModule1,
      dbRecordId: module1Result.dbRecordId,
      feishuUrl: module1Result.feishuUrl,
      feishuAppToken: module1Result.feishuAppToken,
      module1RunId: module1Result.runId,
    }, {
      patentTitle: patentFromModule1?.title ?? null,
      patentNumber: patentFromModule1?.patentNumber ?? null,
    });
    if (!patentTextForReport) {
      patentTextForReport = buildPatentTextSnippet(params, patentFromModule1 ?? null);
    }
    console.log(`[Pipeline ${sessionId}] 步骤1完成 (${stepTimings['step1']}ms), db_record_id: ${module1Result.dbRecordId ?? 'missing'}`);

    if (!module1Result.dbRecordId || module1Result.dbRecordId <= 0) {
      const blockerMessage = [
        '模块1未能将解析结果写入本地数据库，无法继续后续链路。',
        explainModule1LocalBlockers(module1Result),
      ].join(' ');
      await updateStepStatus(sessionId, 2, 'error', blockerMessage);
      throw new Error(blockerMessage);
    }

    // ========== 步骤2：行业识别与路由 ==========
    let detectedIndustry: IndustryType = 'general';
    let industryReasoning = '';
    await updateStepStatus(sessionId, 2, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤2: 行业识别与路由...`);
    const step2Start = Date.now();

    try {
      // 构建行业判断的上下文
      let patentContext = '';

      if (type === 'text' && params.text) {
        // 用户直接输入文本，立即可用
        patentContext = params.text;
      } else if (patentFromModule1) {
        patentContext = [
          patentFromModule1.title,
          patentFromModule1.specification,
          ...(patentFromModule1.independentClaims || []),
          ...(patentFromModule1.dependentClaims || []),
        ]
          .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
          .join('\n\n')
          .slice(0, 4000);
      } else {
        patentContext = `专利文件链接: ${patentFileUrl}`;
      }

      if (!patentContext.trim()) {
        patentContext = `专利文件链接: ${patentFileUrl}`;
      }

      const industryResult = await detectIndustry(
        patentContext,
        (msg) => console.log(`[Pipeline ${sessionId}] 行业识别进度: ${msg}`),
      );

      detectedIndustry = industryResult.industry;
      industryReasoning = industryResult.reasoning;
      stepTimings['step2'] = Date.now() - step2Start;
      console.log(`[Pipeline ${sessionId}] 行业识别结果: ${detectedIndustry} (置信度: ${industryResult.confidence}, 理由: ${industryReasoning}, 耗时 ${stepTimings['step2']}ms)`);

      await updateStepStatus(sessionId, 2, 'completed');
      await updateResults(sessionId, {
        detectedIndustry,
        industryReasoning,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      stepTimings['step2'] = Date.now() - step2Start;
      console.warn(`[Pipeline ${sessionId}] 行业识别异常: ${msg}，回退到通用行业 (${stepTimings['step2']}ms)`);
      detectedIndustry = 'general';
      await updateStepStatus(sessionId, 2, 'error', `行业识别失败，使用通用工作流: ${msg}`);
      await updateResults(sessionId, {
        detectedIndustry: 'general',
        industryReasoning: `识别失败: ${msg}`,
      });
    }

    // ========== 模块2（步骤3）：技术关键词生成（行业路由） ==========
    let module2Result: Module2Result | null = null;
    let module2Error: string | undefined;
    await updateStepStatus(sessionId, 3, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤3: 技术关键词生成（行业: ${detectedIndustry}）...`);
    const step3Start = Date.now();

    try {
      module2Result = await runModule2(
        module1Result.dbRecordId,
        sessionId,
        detectedIndustry,
        (msg) => console.log(`[Pipeline ${sessionId}] 模块2进度: ${msg}`),
      );
      if (module2Result.exceptionMessage) {
        module2Error = module2Result.exceptionMessage;
      }
    } catch (e) {
      module2Error = e instanceof Error ? e.message : String(e);
      console.warn(`[Pipeline ${sessionId}] 模块2异常: ${module2Error}`);
    }

    const keywordsFromDb =
      module1Result.dbRecordId && !module2Error
        ? await getKeywordTexts(module1Result.dbRecordId)
        : null;
    const autoKeywordList = normalizeKeywordList(keywordsFromDb ?? []);
    let keywordList = autoKeywordList;
    if (!module2Error && autoKeywordList.length === 0) {
      module2Error = '模块2未生成任何有效关键词，无法进行商品检索';
    }

    if (module2Error) {
      stepTimings['step3'] = Date.now() - step3Start;
      await updateStepStatus(sessionId, 3, 'error', module2Error);
      await updateResults(sessionId, {
        keywords: keywordList,
        keywordRunId: module2Result?.keywordRunId,
        module2RunId: module2Result?.runId,
        module2Exception: module2Result?.exceptionType || module2Error,
        industryUsed: module2Result?.industryUsed || detectedIndustry,
      });
      console.log(`[Pipeline ${sessionId}] 步骤3异常（使用${module2Result?.industryUsed || detectedIndustry}工作流, 耗时 ${stepTimings['step3']}ms）`);
    } else {
      const keywordConfirmation: KeywordConfirmationState = {
        status: 'timed_wait',
        autoKeywords: autoKeywordList,
        userKeywords: [],
        finalKeywords: autoKeywordList,
        promptedAt: Date.now(),
        deadlineAt: Date.now() + KEYWORD_CONFIRMATION_TIMEOUT_MS,
      };

      await updateStepStatus(sessionId, 3, 'waiting_input');
      await updateResults(sessionId, {
        keywords: autoKeywordList,
        keywordRunId: module2Result?.keywordRunId,
        module2RunId: module2Result?.runId,
        module2Exception: module2Result?.exceptionType || module2Error,
        industryUsed: module2Result?.industryUsed || detectedIndustry,
        keywordConfirmation,
      });

      console.log(`[Pipeline ${sessionId}] 步骤3进入用户确认阶段，等待30秒或用户补充关键词...`);
      const resolvedKeywordState = await waitForKeywordConfirmation(sessionId, autoKeywordList);
      keywordList = normalizeKeywordList(resolvedKeywordState.finalKeywords);
      stepTimings['step3'] = Date.now() - step3Start;

      await updateStepStatus(sessionId, 3, 'completed');
      await updateResults(sessionId, {
        keywords: keywordList,
        keywordRunId: module2Result?.keywordRunId,
        module2RunId: module2Result?.runId,
        module2Exception: module2Result?.exceptionType,
        industryUsed: module2Result?.industryUsed || detectedIndustry,
        keywordConfirmation: {
          ...resolvedKeywordState,
          autoKeywords: normalizeKeywordList(resolvedKeywordState.autoKeywords),
          userKeywords: normalizeKeywordList(resolvedKeywordState.userKeywords),
          finalKeywords: keywordList,
        },
      });
      console.log(`[Pipeline ${sessionId}] 步骤3完成（使用${module2Result?.industryUsed || detectedIndustry}工作流, 最终关键词 ${keywordList.length} 个, 耗时 ${stepTimings['step3']}ms）`);
    }

    // ========== 模块3（步骤4）：商品信息检索 ==========
    let module3Result: Module3Result | null = null;
    let module3Error: string | undefined;
    let module3RecoveredFromTransport = false;
    let module3ProductsCount = 0;
    let module3IsComplete = false;
    await updateStepStatus(sessionId, 4, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤4: 商品信息检索...`);
    const step4Start = Date.now();

    try {
      module3Result = await runModule3(
        module1Result.dbRecordId,
        sessionId,
        keywordList,
        (msg) => console.log(`[Pipeline ${sessionId}] 模块3进度: ${msg}`),
      );
      if (module3Result.exceptionMessage) {
        module3Error = module3Result.exceptionMessage;
      }
      module3ProductsCount = module3Result.totalProductsCount || 0;
      module3IsComplete = module3Result.isComplete ?? false;
    } catch (e) {
      module3Error = e instanceof Error ? e.message : String(e);
      console.warn(`[Pipeline ${sessionId}] 模块3异常: ${module3Error}`);

      if (isRecoverableWorkflowTransportError(module3Error)) {
        console.log(`[Pipeline ${sessionId}] 模块3响应异常，转为轮询数据库中的 search_runs...`);
        const snapshot = await waitForSearchRunRecovery(sessionId, module1Result.dbRecordId, step4Start);
        if (snapshot) {
          module3RecoveredFromTransport = true;
          module3Error = undefined;
          module3ProductsCount = snapshot.total_products_count;
          module3IsComplete = snapshot.is_complete;
          module3Result = {
            searchRunId: snapshot.id,
            totalProductsCount: snapshot.total_products_count,
            isComplete: snapshot.is_complete,
            exceptionMessage: snapshot.error_message || undefined,
            runId: '',
          };
          console.log(
            `[Pipeline ${sessionId}] 模块3已从数据库恢复: search_run_id=${snapshot.id}, products=${snapshot.total_products_count}, complete=${snapshot.is_complete}`,
          );
        }
      }
    }

    if (module1Result.dbRecordId && module3ProductsCount === 0) {
      const latestSearchSnapshot = await getLatestSearchRunSnapshot(sessionId, module1Result.dbRecordId);
      if (latestSearchSnapshot) {
        module3ProductsCount = latestSearchSnapshot.total_products_count;
        module3IsComplete = latestSearchSnapshot.is_complete;
        if (!module3Result) {
          module3Result = {
            searchRunId: latestSearchSnapshot.id,
            totalProductsCount: latestSearchSnapshot.total_products_count,
            isComplete: latestSearchSnapshot.is_complete,
            exceptionMessage: latestSearchSnapshot.error_message || undefined,
            runId: '',
          };
        }
        if (!module3Error && latestSearchSnapshot.error_message) {
          module3Error = latestSearchSnapshot.error_message;
        }
      }
    }

    stepTimings['step4'] = Date.now() - step4Start;
    const shouldContinueToStep5 = module3ProductsCount > 0;
    const step4TerminalMessage = shouldContinueToStep5
      ? (module3RecoveredFromTransport && !module3IsComplete
          ? '模块3 HTTP 响应中断，但后台已写入部分商品数据，继续步骤5'
          : undefined)
      : (module3Error || '步骤4未检索到任何商品，已停止后续步骤');
    await updateStepStatus(sessionId, 4, shouldContinueToStep5 ? 'completed' : 'error', step4TerminalMessage);
    await updateResults(sessionId, {
      products: [],
      searchRunId: module3Result?.searchRunId,
      module3RunId: module3Result?.runId,
      module3Exception: module3Error,
    });
    console.log(
      `[Pipeline ${sessionId}] 步骤4${shouldContinueToStep5 ? '(有商品,继续)' : '(无商品,终止)'} `
      + `(products=${module3ProductsCount}, complete=${module3IsComplete}, 耗时 ${stepTimings['step4']}ms)`,
    );

    if (!shouldContinueToStep5) {
      await updateSessionStatus(sessionId, 'error');
      return;
    }

    // ========== 模块4（步骤5）：技术特征比对 ==========
    let module4Result: Module4Result | null = null;
    let module4Error: string | undefined;
    let module4RecoveredFromTransport = false;
    await updateStepStatus(sessionId, 5, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤5: 技术特征比对（最耗时步骤，可能需要数分钟）...`);
    const step5Start = Date.now();

    try {
      module4Result = await runModule4(
        module1Result.dbRecordId,
        sessionId,
        (msg) => console.log(`[Pipeline ${sessionId}] 模块4进度: ${msg}`),
      );
      if (module4Result.exceptionMessage) {
        module4Error = module4Result.exceptionMessage;
      }
    } catch (e) {
      module4Error = e instanceof Error ? e.message : String(e);
      console.warn(`[Pipeline ${sessionId}] 模块4异常: ${module4Error}`);

      if (isRecoverableWorkflowTransportError(module4Error)) {
        console.log(`[Pipeline ${sessionId}] 模块4响应异常，转为轮询数据库中的 claim_compare_runs...`);
        const snapshot = await waitForClaimCompareRunRecovery(sessionId, module1Result.dbRecordId, step5Start);
        if (snapshot) {
          module4RecoveredFromTransport = true;
          module4Error = undefined;
          module4Result = {
            claimCompareRunId: snapshot.id,
            exceptionMessage: undefined,
            runId: '',
            allComparisonResults: [],
            resultSummary: snapshot.result_summary || '',
            tableUrls: [],
          };
          console.log(
            `[Pipeline ${sessionId}] 模块4已从数据库恢复: claim_compare_run_id=${snapshot.id}, product_count=${snapshot.product_count}`,
          );
        }
      }
    }

    stepTimings['step5'] = Date.now() - step5Start;
    await updateStepStatus(
      sessionId,
      5,
      module4Error ? 'error' : 'completed',
      module4RecoveredFromTransport ? '模块4 HTTP 响应中断，但后台任务已完成并写入数据库' : undefined,
    );
    await updateResults(sessionId, {
      comparisons: [],
      claimCompareRunId: module4Result?.claimCompareRunId,
      module4RunId: module4Result?.runId,
      module4Exception: module4Error,
    });
    console.log(`[Pipeline ${sessionId}] 步骤5${module4RecoveredFromTransport ? '(数据库恢复,继续)' : module4Error ? '异常' : '完成'} (耗时 ${stepTimings['step5']}ms)`);

    // ========== 步骤6：提取分析结果 ==========
    const step6Start = Date.now();
    let patent: PatentInfo | undefined;
    let products: ProductInfo[] = [];
    let comparisons: ProductComparison[] = [];

    // 尝试从搜索商品数据表补充商品详情
    async function enrichProductsFromDb(productsFromComparison: ProductInfo[], sessionIdForDb: string, patentRecordIdForDb: number): Promise<ProductInfo[]> {
      try {
        const searchProductRows = await pgQuery<Record<string, unknown>>(
          `SELECT product_id, product_name, product_url, product_source, price, brand, manufacturer, description, picture
           FROM search_products
           WHERE patent_record_id = $1
             AND analysis_session_id = $2
           ORDER BY id ASC`,
          [patentRecordIdForDb, sessionIdForDb],
        );
        if (searchProductRows.rows.length === 0) return productsFromComparison;

        const enriched = new Map<string, ProductInfo>();
        for (const p of productsFromComparison) {
          enriched.set(p.id, { ...p });
        }
        for (const row of searchProductRows.rows) {
          const spId = String(row['product_id'] || row['id'] || '');
          const spName = String(row['product_name'] || row['name'] || '');
          if (!spId && !spName) continue;
          const key = spId || spName;
          if (enriched.has(key)) {
            const existing = enriched.get(key)!;
            if (!existing.url && row['product_url']) existing.url = String(row['product_url']);
            if (!existing.source && row['product_source']) existing.source = String(row['product_source']);
            if (!existing.price && row['price']) existing.price = String(row['price']);
            if (!existing.description && row['description']) existing.description = String(row['description']);
            if (!existing.imageUrl && row['picture']) {
              try {
                const pics = row['picture'];
                if (typeof pics === 'string') {
                  const parsed = JSON.parse(pics);
                  existing.imageUrl = Array.isArray(parsed) ? parsed[0] : parsed;
                } else if (Array.isArray(pics)) {
                  existing.imageUrl = (pics as unknown[])[0] as string;
                }
              } catch { /* ignore */ }
            }
          } else {
            let imageUrl: string | undefined;
            if (row['picture']) {
              try {
                const pics = row['picture'];
                if (typeof pics === 'string') {
                  const parsed = JSON.parse(pics);
                  imageUrl = Array.isArray(parsed) ? parsed[0] : parsed;
                } else if (Array.isArray(pics)) {
                  imageUrl = (pics as unknown[])[0] as string;
                }
              } catch { /* ignore */ }
            }
            enriched.set(key, {
              id: key,
              name: spName || key,
              url: row['product_url'] ? String(row['product_url']) : undefined,
              source: row['product_source'] ? String(row['product_source']) : undefined,
              price: row['price'] ? String(row['price']) : undefined,
              description: row['description'] ? String(row['description']) : undefined,
              imageUrl,
            });
          }
        }
        return Array.from(enriched.values());
      } catch (err) {
        console.warn(`[Pipeline ${sessionIdForDb}] 补充商品详情失败:`, err);
        return productsFromComparison;
      }
    }

    // 方案1：从模块4 API 响应提取
    if (module4Result && module4Result.allComparisonResults.length > 0) {
      console.log(`[Pipeline ${sessionId}] 模块4返回 ${module4Result.allComparisonResults.length} 条比对结果`);
      comparisons = mapComparisonsFromApi(module4Result.allComparisonResults);

      const productMap = new Map<string, ProductInfo>();
      for (const comp of comparisons) {
        if (!productMap.has(comp.productId)) {
          productMap.set(comp.productId, { id: comp.productId, name: comp.productName });
        }
      }
      products = Array.from(productMap.values());

      // 补充商品详情（图片、URL、描述等）
      if (module1Result.dbRecordId && module1Result.dbRecordId > 0) {
        products = await enrichProductsFromDb(products, sessionId, module1Result.dbRecordId);
      }

      console.log(`[Pipeline ${sessionId}] 从模块4响应提取 ${products.length} 个商品和 ${comparisons.length} 个比对结果`);

      stepTimings['step6'] = Date.now() - step6Start;
      await updateStepStatus(sessionId, 6, 'completed');
    } else {
      // 方案2：模块4 API 无数据时，从 PostgreSQL 数据库读取比对结果
      console.log(`[Pipeline ${sessionId}] 模块4 API 无比对数据，尝试从数据库恢复比对结果...`);

      try {
        const dbComparisonRunId = module4Result?.claimCompareRunId || 0;
        let dbResults: unknown[] = [];

        if (dbComparisonRunId > 0) {
          const rows = await pgQuery<Record<string, unknown>>(
            `SELECT * FROM claim_compare_results WHERE claim_compare_run_id = $1 ORDER BY id ASC`,
            [dbComparisonRunId],
          );
          dbResults = rows.rows;
        } else if (module1Result.dbRecordId && module1Result.dbRecordId > 0) {
          const runRows = await pgQuery<Record<string, unknown>>(
            `SELECT id FROM claim_compare_runs WHERE patent_record_id = $1 ORDER BY id DESC LIMIT 1`,
            [module1Result.dbRecordId],
          );
          if (runRows.rows.length > 0) {
            const runId = runRows.rows[0].id as number;
            const resultRows = await pgQuery<Record<string, unknown>>(
              `SELECT * FROM claim_compare_results WHERE claim_compare_run_id = $1 ORDER BY id ASC`,
              [runId],
            );
            dbResults = resultRows.rows;
          }
        }

        if (dbResults.length > 0) {
          console.log(`[Pipeline ${sessionId}] 从数据库读取到 ${dbResults.length} 条比对结果`);

          // 将数据库行按商品分组转换为嵌套格式，再用 mapComparisonsFromApi 映射
          const grouped = groupDbRowsByProduct(dbResults);
          comparisons = mapComparisonsFromApi(grouped);

          const productMap = new Map<string, ProductInfo>();
          for (const comp of comparisons) {
            if (!productMap.has(comp.productId)) {
              productMap.set(comp.productId, { id: comp.productId, name: comp.productName });
            }
          }
          products = Array.from(productMap.values());

          // 补充商品详情（图片、URL、描述等）
          if (module1Result.dbRecordId && module1Result.dbRecordId > 0) {
            products = await enrichProductsFromDb(products, sessionId, module1Result.dbRecordId);
          }

          console.log(`[Pipeline ${sessionId}] 从数据库恢复 ${products.length} 个商品和 ${comparisons.length} 个比对结果`);

          stepTimings['step6'] = Date.now() - step6Start;
          await updateStepStatus(sessionId, 6, 'completed');
        } else {
          const msg = module4Error || module4Result?.resultSummary || '模块4未返回可用的比对数据，数据库中也无比对结果';
          console.warn(`[Pipeline ${sessionId}] ${msg}`);
          stepTimings['step6'] = Date.now() - step6Start;
          await updateStepStatus(sessionId, 6, 'error', msg);
        }
      } catch (dbError) {
        const msg = module4Error || module4Result?.resultSummary || '模块4未返回可用的比对数据';
        const errorMsg = dbError instanceof Error ? dbError.message : String(dbError);
        console.warn(`[Pipeline ${sessionId}] ${msg}，数据库恢复失败: ${errorMsg}`);
        stepTimings['step6'] = Date.now() - step6Start;
        await updateStepStatus(sessionId, 6, 'error', `${msg}，数据库恢复失败: ${errorMsg}`);
      }
    }

    // ========== 汇总完成 ==========
    const totalTime = Date.now() - pipelineStart;
    await updateSessionStatus(sessionId, 'completed');
    await updateResults(sessionId, {
      patent,
      products,
      comparisons,
      module2Exception: module2Result?.exceptionType || module2Error,
      module3Exception: module3Result?.exceptionType || module3Error,
      module4Exception: module4Result?.exceptionType || module4Error,
    });

    // 输出性能摘要
    console.log(`[Pipeline ${sessionId}] 分析完成! ${products.length} 个商品, ${comparisons.length} 个比对`);
    console.log(`[Pipeline ${sessionId}] 性能摘要 (总耗时 ${totalTime}ms): ${Object.entries(stepTimings).map(([k, v]) => `${k}=${v}ms`).join(', ')}`);
  } catch (error) {
    const totalTime = Date.now() - pipelineStart;
    console.error(`[Pipeline ${sessionId}] 致命错误 (总耗时 ${totalTime}ms):`, error);
    await updateSessionStatus(sessionId, 'error');
    await updateResults(sessionId, {
      module2Exception: error instanceof Error ? error.message : String(error),
    });
    await reportPipelineFailure({
      sessionId,
      error,
      patentText: patentTextForReport ?? buildPatentTextSnippet(params, null),
      inputType: type,
      inputValue: inputValueForReport,
      fileUrl: fileUrlForReport,
      meta: {
        totalTimeMs: totalTime,
        stepTimings,
      },
    });
  }
}

// ============================================================
// POST /api/analyze — 触发后台分析，立即返回 sessionId
// ============================================================

export async function POST(request: NextRequest): Promise<NextResponse> {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return createUnauthorizedResponse(request);
  }

  const body = await request.json();
  const { type, url, fileKey, fileName, fileUrl, text } = body as {
    type: 'url' | 'file' | 'text';
    url?: string;
    fileKey?: string;
    fileName?: string;
    fileUrl?: string;
    text?: string;
  };

  // 验证输入
  if (type === 'url' && !url) {
    return NextResponse.json({ error: '缺少专利 URL' }, { status: 400 });
  }
  if (type === 'file' && !fileKey) {
    return NextResponse.json({ error: '缺少上传文件信息' }, { status: 400 });
  }
  if (type === 'text' && !text) {
    return NextResponse.json({ error: '缺少专利文本内容' }, { status: 400 });
  }

  // 创建分析会话
  const session = await createSession({
    type,
    value: type === 'url' ? url! : type === 'text' ? text! : fileKey!,
    fileName,
    fileUrl,
    text,
  }, currentUser);

  const sessionId = session.id;

  // 后台异步执行分析流水线（不阻塞响应）
  // 使用 setImmediate 确保在当前请求完成后才启动
  setImmediate(() => {
    executePipeline(sessionId, type, { url, fileKey, fileName, fileUrl, text }).catch((err) => {
      console.error(`[Pipeline ${sessionId}] 未捕获异常:`, err);
      void updateSessionStatus(sessionId, 'error');
      void reportPipelineFailure({
        sessionId,
        error: err,
        inputType: type,
        inputValue: type === 'url' ? url ?? null : type === 'file' ? fileKey ?? null : text ?? null,
        fileUrl: type === 'file' ? fileUrl ?? null : null,
      });
    });
  });

  // 立即返回 sessionId，前端通过轮询获取进度
  return NextResponse.json({
    success: true,
    sessionId,
    message: '分析已启动，请轮询 /api/analysis/{sessionId} 获取进度',
  });
}
