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
  startModule3Async,
  getModule3RunStatus,
  startModule4Async,
  getModule4RunStatus,
  detectIndustry,
  warmupCozeSearch,
} from '@/lib/workflow-client';
import type {
  Module1Result,
  Module2Result,
  Module3AsyncStartResult,
  Module3Result,
  Module3RunStatusResult,
  Module3TaskStatus,
  Module4Result,
  Module4TaskStatus,
} from '@/lib/workflow-client';
import { buildFallbackTokenUnits, computeClaimScores, computeProductScore as computeWeightedProductScore } from '@/lib/claim-score';
import { scoreToBand, scoreToRiskLevel } from '@/lib/types';
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
    claimScores: ProductComparison['claimScores'];
    productSimilarityScore?: number;
    productScoreBand?: ProductComparison['productScoreBand'];
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
        productMap.set(id, { productId: id, productName: productName || id, elements: [], claimScores: [] });
      }
      const group = productMap.get(id)!;
      const productScore = getScoreField(record, 'product_similarity_score');
      if (productScore != null) {
        group.productSimilarityScore = productScore;
        // product 级 score_band 暂不重算，依赖 computeProductScore 时基于 claimScores 再算
        group.productScoreBand = normalizeScoreBand(getStringField(record, 'product_score_band'));
      }

      const claimScoresRaw = record['claim_scores'];
      if (Array.isArray(claimScoresRaw)) {
        group.claimScores = claimScoresRaw
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
          .map((item) => {
            const claimScore = getScoreField(item, 'similarity_score', 'score') ?? 0;
            const claimMatched = getScoreField(item, 'claim_matched_effective_length') ?? 0;
            const claimTotal = getScoreField(item, 'claim_total_effective_length') ?? 0;
            const claimZeroed = getBooleanField(item, 'zeroed_by_mismatch');
            return {
              claimId: getStringField(item, 'claim_id', 'claimId') || 'unknown',
              similarityScore: claimScore,
              scoreBand: normalizeScoreBand(getStringField(item, 'score_band', 'scoreBand'))
                || scoreToBand(claimScore, {
                  zeroedByMismatch: claimZeroed,
                  matchedEffectiveLength: claimMatched,
                  totalEffectiveLength: claimTotal,
                }),
              claimTotalEffectiveLength: claimTotal,
              claimMatchedEffectiveLength: claimMatched,
              zeroedByMismatch: claimZeroed,
            };
          });
      }

      for (const feat of features) {
        if (!feat || typeof feat !== 'object') continue;
        const f = feat as Record<string, unknown>;

        const featureText = getStringField(f, 'feature_text', '权利要求特征', '专利技术特征', 'featureElement', 'claimElement', 'claim_element');
        const evidence = getStringField(f, 'evidence', '商品特征', '产品技术特征', 'productFeature', 'product_feature', 'product_feature_evidence');
        const reason = getStringField(f, 'reason', '推理过程', '比对分析', '分析', 'reasoning');
        const reasoningType = getStringField(f, 'reasoning_type', '推理类型');
        const featureId = getStringField(f, 'feature_id', '特征编号');
        const similarityScore = getScoreField(f, 'similarity_score', 'similarityScore')
          ?? legacyStatusToScore(getStringField(f, 'comparison_result', 'status', '匹配状态', '比对结果', 'matchStatus', 'result'));
        const featMatchedLength = getScoreField(f, 'matched_effective_length') ?? 0;
        const featTotalLength = getScoreField(f, 'feature_effective_length') ?? 0;
        const featZeroed = getBooleanField(f, 'zeroed_by_mismatch');
        const scoreBand = normalizeScoreBand(getStringField(f, 'score_band', 'scoreBand'))
          || scoreToBand(similarityScore, {
            zeroedByMismatch: featZeroed,
            matchedEffectiveLength: featMatchedLength,
            totalEffectiveLength: featTotalLength,
          });
        const tokenUnits = parseTokenUnits(f['token_units'], featureText || '');

        // 提取 evidence_images
        const evidenceImagesRaw = f['evidence_images'];
        const evidenceImages: string[] = Array.isArray(evidenceImagesRaw)
          ? evidenceImagesRaw.filter((u): u is string => typeof u === 'string' && u.trim().length > 0)
          : [];

        // feature_text 作为权利要求特征描述，evidence 作为商品侧证据
        group.elements.push({
          featureId: featureId || undefined,
          claimElement: featureText || '',
          productFeature: evidence || '',
          similarityScore,
          scoreBand,
          reasoning: [reason, reasoningType].filter(Boolean).join(' | ') || '',
          patentReference: getStringField(f, 'claim_id') || undefined,
          evidenceImages: evidenceImages.length > 0 ? evidenceImages : undefined,
          scoreRationale: getStringField(f, 'score_rationale', 'scoreRationale') || undefined,
          tokenUnits,
          scoreDetail: {
            fullScore: getScoreField(f, 'feature_full_score') ?? similarityScore,
            awardedScore: getScoreField(f, 'feature_awarded_score') ?? similarityScore,
            effectiveLength: featTotalLength,
            matchedEffectiveLength: featMatchedLength,
            zeroedByMismatch: featZeroed,
          },
          isLegacyScore: !('token_units' in f) && !('feature_full_score' in f),
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
          productMap.set(id, { productId: id, productName: productName || id, elements: [], claimScores: [] });
        }
      }
      continue;
    }

    const id = productId || `product_${productMap.size + 1}`;
    if (!productMap.has(id)) {
      productMap.set(id, { productId: id, productName: productName || id, elements: [], claimScores: [] });
    }
    const similarityScore = getScoreField(record, 'similarity_score', 'similarityScore')
      ?? legacyStatusToScore(getStringField(record, 'status', '匹配状态', '比对结果', 'matchStatus', 'comparison_result'));
    const flatMatched = getScoreField(record, 'matched_effective_length') ?? 0;
    const flatTotal = getScoreField(record, 'feature_effective_length') ?? 0;
    const flatZeroed = getBooleanField(record, 'zeroed_by_mismatch');

    productMap.get(id)!.elements.push({
      claimElement: claimElement || '',
      productFeature: productFeature || '',
      similarityScore,
      scoreBand: normalizeScoreBand(getStringField(record, 'score_band', 'scoreBand'))
        || scoreToBand(similarityScore, {
          zeroedByMismatch: flatZeroed,
          matchedEffectiveLength: flatMatched,
          totalEffectiveLength: flatTotal,
        }),
      reasoning: reasoning || '',
      patentReference: getStringField(record, 'claim_id', 'patent_reference') || undefined,
      scoreRationale: getStringField(record, 'score_rationale', 'scoreRationale') || undefined,
      tokenUnits: parseTokenUnits(record['token_units'], claimElement || ''),
      scoreDetail: {
        fullScore: getScoreField(record, 'feature_full_score') ?? similarityScore,
        awardedScore: getScoreField(record, 'feature_awarded_score') ?? similarityScore,
        effectiveLength: flatTotal,
        matchedEffectiveLength: flatMatched,
        zeroedByMismatch: flatZeroed,
      },
      isLegacyScore: !('token_units' in record) && !('feature_full_score' in record),
    });
  }

  return Array.from(productMap.values()).map(group => {
    const claimScores = group.claimScores.length > 0 ? group.claimScores : computeClaimScores(group.elements);
    const weighted = computeWeightedProductScore(claimScores);
    const productScore = group.productSimilarityScore ?? weighted.productSimilarityScore;
    const productScoreBand = group.productScoreBand ?? weighted.productScoreBand;
    // 通过 scoreBand 反推 zeroed / matched / total，使 scoreToRiskLevel 也能正确区分"待确认"和"明确不相同"
    const productZeroed = claimScores.some((c) => c.zeroedByMismatch);
    const productMatched = claimScores.reduce((s, c) => s + (c.claimMatchedEffectiveLength ?? 0), 0);
    const productTotal = claimScores.reduce((s, c) => s + (c.claimTotalEffectiveLength ?? 0), 0);
    const riskLevel = scoreToRiskLevel(productScore, {
      zeroedByMismatch: productZeroed,
      matchedEffectiveLength: productMatched,
      totalEffectiveLength: productTotal,
    });
    return {
      productId: group.productId,
      productName: group.productName,
      productSimilarityScore: productScore,
      productScoreBand,
      riskLevel,
      claimElements: group.elements,
      claimScores,
      highestScoringClaimId: weighted.highestScoringClaimId,
      isLegacyScore: group.elements.every((item) => item.isLegacyScore),
    };
  });
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

function getScoreField(record: Record<string, unknown>, ...candidates: string[]): number | undefined {
  for (const key of candidates) {
    const value = record[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return Math.max(0, Math.min(100, Number(value.toFixed(2))));
    }
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value.trim());
      if (Number.isFinite(parsed)) {
        return Math.max(0, Math.min(100, Number(parsed.toFixed(2))));
      }
    }
  }
  return undefined;
}

function getBooleanField(record: Record<string, unknown>, ...candidates: string[]): boolean | undefined {
  for (const key of candidates) {
    const value = record[key];
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
      if (value === 'true') return true;
      if (value === 'false') return false;
    }
  }
  return undefined;
}

function legacyStatusToScore(status: string): number {
  if (!status) return 50;
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
    return 0;
  }
  if (
    lower.includes('匹配')
    || lower.includes('matching')
    || /\bmatch\b/.test(lower)
    || lower.includes('相同')
    || lower.includes('一致')
    || lower.includes('等同')
  ) {
    return 100;
  }
  return 50;
}

function normalizeScoreBand(band: string): ProductComparison['claimElements'][0]['scoreBand'] | undefined {
  const lower = band.trim().toLowerCase();
  if (!lower) return undefined;
  if (lower.includes('明确相同') || lower.includes('exact_match')) return 'exact_match';
  if (lower.includes('明确不相同') || lower.includes('exact_mismatch')) return 'exact_mismatch';
  if (lower.includes('高相似') || lower.includes('high_similarity')) return 'high_similarity';
  if (lower.includes('中等相似') || lower.includes('medium_similarity')) return 'medium_similarity';
  if (lower.includes('低相似') || lower.includes('low_similarity')) return 'low_similarity';
  if (lower.includes('待确认') || lower.includes('uncertain')) return 'uncertain';
  return undefined;
}

function parseTokenUnits(value: unknown, claimElement: string) {
  if (Array.isArray(value)) {
    return value
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => ({
        text: getStringField(item, 'text'),
        normalizedText: getStringField(item, 'normalized_text', 'normalizedText') || undefined,
        status: normalizeUnitStatus(getStringField(item, 'status', 'unit_status')),
        evidence: getStringField(item, 'evidence') || undefined,
        reason: getStringField(item, 'reason') || undefined,
        start: getScoreField(item, 'start'),
        end: getScoreField(item, 'end'),
        effectiveLength: getScoreField(item, 'effective_length', 'effectiveLength'),
      }))
      .filter((item) => item.text);
  }
  return buildFallbackTokenUnits(claimElement);
}

function normalizeUnitStatus(status: string): 'match' | 'mismatch' | 'uncertain' {
  const lower = status.trim().toLowerCase();
  if (['match', 'matching', '相同', '明确相同'].includes(lower)) return 'match';
  if (['mismatch', 'not_match', '不同', '不相同', '明确不相同'].includes(lower)) return 'mismatch';
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
    if (r['similarity_score'] != null) feature['similarity_score'] = r['similarity_score'];
    if (r['score_band'] != null) feature['score_band'] = r['score_band'];
    if (r['feature_full_score'] != null) feature['feature_full_score'] = r['feature_full_score'];
    if (r['feature_awarded_score'] != null) feature['feature_awarded_score'] = r['feature_awarded_score'];
    if (r['feature_effective_length'] != null) feature['feature_effective_length'] = r['feature_effective_length'];
    if (r['matched_effective_length'] != null) feature['matched_effective_length'] = r['matched_effective_length'];
    if (r['claim_total_effective_length'] != null) feature['claim_total_effective_length'] = r['claim_total_effective_length'];
    if (r['zeroed_by_mismatch'] != null) feature['zeroed_by_mismatch'] = r['zeroed_by_mismatch'];
    if (r['reason'] != null) feature['reason'] = r['reason'];
    if (r['reasoning_type'] != null) feature['reasoning_type'] = r['reasoning_type'];
    if (r['evidence_images'] != null) feature['evidence_images'] = r['evidence_images'];
    if (r['token_units'] != null) feature['token_units'] = r['token_units'];
    if (r['raw_payload'] != null && typeof r['raw_payload'] === 'object') {
      const payload = r['raw_payload'] as Record<string, unknown>;
      for (const k of ['feature_id', 'feature_text', 'claim_id', 'evidence', 'comparison_result', 'similarity_score', 'score_band', 'feature_full_score', 'feature_awarded_score', 'feature_effective_length', 'matched_effective_length', 'claim_total_effective_length', 'zeroed_by_mismatch', 'reason', 'reasoning_type', 'evidence_images', 'token_units', 'score_rationale']) {
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
  status: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
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
      select id, product_count, result_summary, status, error_message, started_at, finished_at
      from claim_compare_runs
      where analysis_session_id = $1 and patent_record_id = $2
      order by id desc
      limit 1
    `,
    [sessionId, patentRecordId],
  );
  return result.rows[0] || null;
}

interface Module4ExecutionOutcome {
  module4Result: Module4Result | null;
  module4Error?: string;
  module4RecoveredFromTransport: boolean;
  module4TaskStatus?: Module4TaskStatus;
  module4TaskStartedAt?: string;
  module4TaskFinishedAt?: string;
}

interface ComparisonArtifacts {
  products: ProductInfo[];
  comparisons: ProductComparison[];
  claimCompareRunId?: number;
  errorMessage?: string;
}

async function runModule4Task(input: {
  sessionId: string;
  patentRecordId: number;
  runId: string;
  progressLabel: string;
}): Promise<Module4ExecutionOutcome> {
  const {
    sessionId,
    patentRecordId,
    runId,
    progressLabel,
  } = input;
  let module4Result: Module4Result | null = null;
  let module4Error: string | undefined;
  let module4RecoveredFromTransport = false;
  let module4TaskStatus: Module4TaskStatus | undefined;
  let module4TaskStartedAt: string | undefined;
  let module4TaskFinishedAt: string | undefined;

  try {
    const module4Task = await startModule4Async(
      patentRecordId,
      sessionId,
      runId,
      (msg) => console.log(`[Pipeline ${sessionId}] ${progressLabel}: ${msg}`),
    );
    module4Result = {
      claimCompareRunId: module4Task.claimCompareRunId,
      exceptionMessage: undefined,
      runId: module4Task.runId,
      allComparisonResults: [],
      resultSummary: '',
      tableUrls: [],
    };
    module4TaskStatus = module4Task.status === 'accepted' ? 'queued' : module4Task.status;
    await updateResults(sessionId, {
      claimCompareRunId: module4Result.claimCompareRunId,
      module4RunId: module4Result.runId,
      module4TaskStatus,
      module4TaskStartedAt,
      module4TaskFinishedAt,
      module4TaskError: undefined,
      module4Exception: undefined,
    });

    let consecutiveStatusFailures = 0;
    let lastObservedStatus: string | undefined;
    let lastObservedClaimCompareRunId = module4Result.claimCompareRunId;
    let lastObservedStartedAt: string | undefined;
    let lastObservedFinishedAt: string | undefined;
    let lastObservedTaskError: string | undefined;

    while (true) {
      try {
        const runStatus = await getModule4RunStatus(module4Result.runId);
        consecutiveStatusFailures = 0;
        module4TaskStatus = runStatus.status;
        module4TaskStartedAt = runStatus.startedAt;
        module4TaskFinishedAt = runStatus.finishedAt;
        if (runStatus.claimCompareRunId) {
          module4Result.claimCompareRunId = runStatus.claimCompareRunId;
        }
        module4Result.resultSummary = runStatus.resultSummary;

        const taskError = runStatus.errorMessage;
        const shouldPersistStatus = (
          lastObservedStatus !== runStatus.status
          || lastObservedClaimCompareRunId !== module4Result.claimCompareRunId
          || lastObservedStartedAt !== module4TaskStartedAt
          || lastObservedFinishedAt !== module4TaskFinishedAt
          || lastObservedTaskError !== taskError
        );
        if (shouldPersistStatus) {
          await updateResults(sessionId, {
            claimCompareRunId: module4Result.claimCompareRunId,
            module4RunId: module4Result.runId,
            module4TaskStatus: runStatus.status,
            module4TaskStartedAt,
            module4TaskFinishedAt,
            module4TaskError: taskError,
            module4Exception: undefined,
          });
          lastObservedStatus = runStatus.status;
          lastObservedClaimCompareRunId = module4Result.claimCompareRunId;
          lastObservedStartedAt = module4TaskStartedAt;
          lastObservedFinishedAt = module4TaskFinishedAt;
          lastObservedTaskError = taskError;
        }

        if (runStatus.status === 'completed') {
          module4Error = undefined;
          break;
        }

        if (runStatus.status === 'error' || runStatus.status === 'cancelled' || runStatus.status === 'timeout') {
          module4Error = taskError || `模块4任务状态异常: ${runStatus.status}`;
          break;
        }
      } catch (statusError) {
        consecutiveStatusFailures += 1;
        const statusErrorMessage = statusError instanceof Error ? statusError.message : String(statusError);
        console.warn(
          `[Pipeline ${sessionId}] ${progressLabel} 状态查询失败 (${consecutiveStatusFailures}): ${statusErrorMessage}`,
        );

        if (consecutiveStatusFailures >= 5) {
          const snapshot = await getLatestClaimCompareRunSnapshot(sessionId, patentRecordId);
          if (snapshot && snapshot.status) {
            module4RecoveredFromTransport = true;
            module4TaskStatus = snapshot.status as Module4TaskStatus;
            module4TaskStartedAt = snapshot.started_at || undefined;
            module4TaskFinishedAt = snapshot.finished_at || undefined;
            module4Result.claimCompareRunId = snapshot.id;
            module4Result.resultSummary = snapshot.result_summary || '';
            await updateResults(sessionId, {
              claimCompareRunId: snapshot.id,
              module4RunId: module4Result.runId,
              module4TaskStatus,
              module4TaskStartedAt,
              module4TaskFinishedAt,
              module4TaskError: snapshot.error_message || undefined,
              module4Exception: undefined,
            });
            if (snapshot.status === 'completed') {
              module4Error = undefined;
              break;
            }
            if (snapshot.status === 'error' || snapshot.status === 'cancelled' || snapshot.status === 'timeout') {
              module4Error = snapshot.error_message || `模块4任务状态异常: ${snapshot.status}`;
              break;
            }
          }
        }

        if (consecutiveStatusFailures >= 12) {
          throw new Error(`模块4状态查询连续失败: ${statusErrorMessage}`);
        }
      }

      await sleep(5000);
    }
  } catch (error) {
    module4Error = error instanceof Error ? error.message : String(error);
    console.warn(`[Pipeline ${sessionId}] ${progressLabel} 异常: ${module4Error}`);
  }

  return {
    module4Result,
    module4Error,
    module4RecoveredFromTransport,
    module4TaskStatus,
    module4TaskStartedAt,
    module4TaskFinishedAt,
  };
}

async function enrichProductsFromDb(
  productsFromComparison: ProductInfo[],
  sessionIdForDb: string,
  patentRecordIdForDb: number,
): Promise<ProductInfo[]> {
  try {
    const searchProductRows = await pgQuery<Record<string, unknown>>(
      `SELECT id, product_id, product_name, product_url, product_source, price, brand, manufacturer, description, picture
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
      const searchRowId = row['id'] != null ? String(row['id']) : '';
      const spId = String(row['product_id'] || '');
      const spName = String(row['product_name'] || row['name'] || '');
      const brand = row['brand'] ? String(row['brand']) : undefined;
      const manufacturer = row['manufacturer'] ? String(row['manufacturer']) : undefined;
      if (!searchRowId && !spId && !spName) continue;
      const key = searchRowId || spId || spName;
      if (enriched.has(key)) {
        const existing = enriched.get(key)!;
        if (!existing.url && row['product_url']) existing.url = String(row['product_url']);
        if (!existing.source && row['product_source']) existing.source = String(row['product_source']);
        if (!existing.price && row['price']) existing.price = String(row['price']);
        if (!existing.brand && brand) existing.brand = brand;
        if (!existing.manufacturer && manufacturer) existing.manufacturer = manufacturer;
        if (!existing.company && manufacturer) existing.company = manufacturer;
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
          brand,
          manufacturer,
          company: manufacturer,
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

async function loadSearchProductsForSession(
  sessionIdForDb: string,
  patentRecordIdForDb: number,
): Promise<ProductInfo[]> {
  return enrichProductsFromDb([], sessionIdForDb, patentRecordIdForDb);
}

async function loadComparisonArtifacts(
  sessionId: string,
  patentRecordId: number,
  module4Result: Module4Result | null,
  module4Error?: string,
): Promise<ComparisonArtifacts> {
  let products: ProductInfo[] = [];
  let comparisons: ProductComparison[] = [];

  if (module4Result && module4Result.allComparisonResults.length > 0) {
    comparisons = mapComparisonsFromApi(module4Result.allComparisonResults);
    const productMap = new Map<string, ProductInfo>();
    for (const comp of comparisons) {
      if (!productMap.has(comp.productId)) {
        productMap.set(comp.productId, { id: comp.productId, name: comp.productName });
      }
    }
    products = Array.from(productMap.values());
    if (patentRecordId > 0) {
      products = await enrichProductsFromDb(products, sessionId, patentRecordId);
    }
    return {
      products,
      comparisons,
      claimCompareRunId: module4Result.claimCompareRunId,
    };
  }

  const dbComparisonRunId = module4Result?.claimCompareRunId || 0;
  let dbResults: unknown[] = [];
  let recoveredDbComparisonRunId = dbComparisonRunId;

  if (dbComparisonRunId > 0) {
    const rows = await pgQuery<Record<string, unknown>>(
      `SELECT * FROM claim_compare_results WHERE claim_compare_run_id = $1 ORDER BY id ASC`,
      [dbComparisonRunId],
    );
    dbResults = rows.rows;
  } else if (patentRecordId > 0) {
    const runRows = await pgQuery<Record<string, unknown>>(
      `SELECT id
       FROM claim_compare_runs
       WHERE patent_record_id = $1
         AND analysis_session_id = $2
       ORDER BY id DESC
       LIMIT 1`,
      [patentRecordId, sessionId],
    );
    if (runRows.rows.length > 0) {
      const runId = runRows.rows[0].id as number;
      recoveredDbComparisonRunId = runId;
      const resultRows = await pgQuery<Record<string, unknown>>(
        `SELECT * FROM claim_compare_results WHERE claim_compare_run_id = $1 ORDER BY id ASC`,
        [runId],
      );
      dbResults = resultRows.rows;
    }
  }

  if (dbResults.length === 0 && patentRecordId > 0) {
    const fallbackRunRows = await pgQuery<Record<string, unknown>>(
      `SELECT r.id
       FROM claim_compare_runs r
       WHERE r.patent_record_id = $1
         AND r.analysis_session_id = $2
         AND EXISTS (
           SELECT 1
           FROM claim_compare_results rr
           WHERE rr.claim_compare_run_id = r.id
         )
       ORDER BY r.id DESC
       LIMIT 1`,
      [patentRecordId, sessionId],
    );
    if (fallbackRunRows.rows.length > 0) {
      const fallbackRunId = fallbackRunRows.rows[0].id as number;
      const fallbackResultRows = await pgQuery<Record<string, unknown>>(
        `SELECT * FROM claim_compare_results WHERE claim_compare_run_id = $1 ORDER BY id ASC`,
        [fallbackRunId],
      );
      if (fallbackResultRows.rows.length > 0) {
        recoveredDbComparisonRunId = fallbackRunId;
        dbResults = fallbackResultRows.rows;
      }
    }
  }

  if (dbResults.length === 0) {
    return {
      products: [],
      comparisons: [],
      claimCompareRunId: recoveredDbComparisonRunId || undefined,
      errorMessage: module4Error || module4Result?.resultSummary || '模块4未返回可用的比对数据，数据库中也无比对结果',
    };
  }

  const grouped = groupDbRowsByProduct(dbResults);
  comparisons = mapComparisonsFromApi(grouped);
  const productMap = new Map<string, ProductInfo>();
  for (const comp of comparisons) {
    if (!productMap.has(comp.productId)) {
      productMap.set(comp.productId, { id: comp.productId, name: comp.productName });
    }
  }
  products = Array.from(productMap.values());
  if (patentRecordId > 0) {
    products = await enrichProductsFromDb(products, sessionId, patentRecordId);
  }

  return {
    products,
    comparisons,
    claimCompareRunId: recoveredDbComparisonRunId || undefined,
  };
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
const MODULE3_INITIAL_WAIT_MS = 20 * 60 * 1000;
const MODULE3_POLL_INTERVAL_MS = 5_000;
const MODULE3_EXTENDED_POLL_LOG_INTERVAL = 60_000;

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
    let module3TaskStatus: Module3TaskStatus | undefined;
    const module3TaskStartedAt = new Date().toISOString();
    let module3TaskFinishedAt: string | undefined;
    let module3Error: string | undefined;
    let module3ProductsCount = 0;
    let module3IsComplete = false;
    let initialWaitExceeded = false;
    let lastExtendedWaitLogAt = Date.now();
    let initialArtifacts: ComparisonArtifacts | null = null;

    await updateStepStatus(sessionId, 4, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤4: 商品信息检索...`);
    const step4Start = Date.now();

    const module3Task: Module3AsyncStartResult = await startModule3Async(
      module1Result.dbRecordId,
      sessionId,
      `${sessionId}-module3`,
      keywordList,
      (msg) => console.log(`[Pipeline ${sessionId}] 模块3进度: ${msg}`),
    );
    module3TaskStatus = module3Task.status === 'accepted' ? 'queued' : module3Task.status;
    module3Result = {
      searchRunId: module3Task.searchRunId,
      totalProductsCount: 0,
      isComplete: false,
      exceptionMessage: undefined,
      runId: module3Task.runId,
    };
    await updateResults(sessionId, {
      searchRunId: module3Result.searchRunId,
      module3RunId: module3Result.runId,
      module3TaskStatus,
      module3TaskStartedAt,
      module3TaskFinishedAt,
      module3TaskError: undefined,
      module3Exception: undefined,
    });

    let initialStep5Triggered = false;
    let initialStep5Completed = false;
    let lastPersistedModule3Status: string | undefined;
    let lastPersistedModule3ProductsCount = -1;
    let lastPersistedModule3RunId = module3Result.searchRunId;
    let lastPersistedModule3Error: string | undefined;

    while (true) {
      let runStatus: Module3RunStatusResult | null = null;
      try {
        runStatus = await getModule3RunStatus(module3Result.runId);
      } catch (statusError) {
        const statusErrorMessage = statusError instanceof Error ? statusError.message : String(statusError);
        console.warn(`[Pipeline ${sessionId}] 模块3状态查询失败: ${statusErrorMessage}`);
      }

      const latestSearchSnapshot = await getLatestSearchRunSnapshot(sessionId, module1Result.dbRecordId);
      if (latestSearchSnapshot) {
        module3ProductsCount = Math.max(module3ProductsCount, latestSearchSnapshot.total_products_count);
        module3IsComplete = module3IsComplete || latestSearchSnapshot.is_complete;
        if (!module3Result.searchRunId) {
          module3Result.searchRunId = latestSearchSnapshot.id;
        }
        if (!module3Error && latestSearchSnapshot.error_message) {
          module3Error = latestSearchSnapshot.error_message;
        }
      }

      if (runStatus) {
        module3TaskStatus = runStatus.status;
        if (runStatus.searchRunId && !module3Result.searchRunId) {
          module3Result.searchRunId = runStatus.searchRunId;
        }
        if (runStatus.totalProductsCount > module3ProductsCount) {
          module3ProductsCount = runStatus.totalProductsCount;
        }
        if (typeof runStatus.isComplete === 'boolean') {
          module3IsComplete = runStatus.isComplete;
        } else if (runStatus.status === 'completed') {
          module3IsComplete = true;
        }
        if (runStatus.status === 'completed' || runStatus.status === 'error' || runStatus.status === 'cancelled' || runStatus.status === 'timeout') {
          module3TaskFinishedAt = module3TaskFinishedAt || new Date().toISOString();
        }
        if (runStatus.errorMessage) {
          module3Error = runStatus.errorMessage;
        }
      }

      module3Result.totalProductsCount = module3ProductsCount;
      module3Result.isComplete = module3IsComplete;
      module3Result.exceptionMessage = module3Error;

      const shouldPersistModule3State = (
        lastPersistedModule3Status !== module3TaskStatus
        || lastPersistedModule3ProductsCount !== module3ProductsCount
        || lastPersistedModule3RunId !== module3Result.searchRunId
        || lastPersistedModule3Error !== module3Error
      );
      if (shouldPersistModule3State) {
        await updateResults(sessionId, {
          searchRunId: module3Result.searchRunId,
          module3RunId: module3Result.runId,
          module3TaskStatus,
          module3TaskStartedAt,
          module3TaskFinishedAt,
          module3TaskError: module3Error,
          module3Exception: module3Error,
        });
        lastPersistedModule3Status = module3TaskStatus;
        lastPersistedModule3ProductsCount = module3ProductsCount;
        lastPersistedModule3RunId = module3Result.searchRunId;
        lastPersistedModule3Error = module3Error;
      }

      if (!initialStep5Triggered && module3ProductsCount > 0 && !module3IsComplete) {
        initialStep5Triggered = true;
        await updateStepStatus(sessionId, 4, 'partial', '已检索到部分商品，后台继续检索中');
        break;
      }

      if (!initialWaitExceeded && (Date.now() - step4Start) >= MODULE3_INITIAL_WAIT_MS && module3ProductsCount === 0 && !module3IsComplete) {
        initialWaitExceeded = true;
        lastExtendedWaitLogAt = Date.now();
        console.log(`[Pipeline ${sessionId}] 步骤4首个等待上限已到，仍未检索到商品，继续等待后台检索结果...`);
        await updateStepStatus(sessionId, 4, 'running', '首个等待上限已到，暂未检索到商品，继续等待后台检索结果');
      }

      if (initialWaitExceeded && module3ProductsCount === 0 && (Date.now() - lastExtendedWaitLogAt) >= MODULE3_EXTENDED_POLL_LOG_INTERVAL) {
        lastExtendedWaitLogAt = Date.now();
        console.log(`[Pipeline ${sessionId}] 步骤4仍未检索到商品，后台继续轮询中...`);
      }

      const module3ReachedTerminal =
        module3TaskStatus === 'completed'
        || module3TaskStatus === 'error'
        || module3TaskStatus === 'cancelled'
        || module3TaskStatus === 'timeout';

      if (module3ReachedTerminal || module3IsComplete) {
        module3TaskFinishedAt = module3TaskFinishedAt || new Date().toISOString();
        break;
      }

      await sleep(MODULE3_POLL_INTERVAL_MS);
    }

    if (initialStep5Triggered) {
      await updateStepStatus(sessionId, 5, 'running');
      console.log(`[Pipeline ${sessionId}] 步骤5: 基于当前商品启动首轮分析...`);
      const step5InitialStart = Date.now();
      const initialOutcome = await runModule4Task({
        sessionId,
        patentRecordId: module1Result.dbRecordId,
        runId: `${sessionId}-module4-initial`,
        progressLabel: '模块4首轮进度',
      });
      stepTimings['step5_initial'] = Date.now() - step5InitialStart;

      if (initialOutcome.module4RecoveredFromTransport) {
        console.warn(
          `[Pipeline ${sessionId}] 模块4首轮 HTTP 响应中断，但后台任务已完成并写入数据库 `
          + `(耗时 ${stepTimings['step5_initial']}ms, claim_compare_run_id=${initialOutcome.module4Result?.claimCompareRunId || 'unknown'})`,
        );
      }

      if (initialOutcome.module4Error) {
        await updateStepStatus(sessionId, 5, 'error', initialOutcome.module4Error);
        await updateResults(sessionId, {
          initialClaimCompareRunId: initialOutcome.module4Result?.claimCompareRunId,
          claimCompareRunId: initialOutcome.module4Result?.claimCompareRunId,
          step5Phase: 'initial',
          partialAnalysisAvailable: false,
          module4RunId: initialOutcome.module4Result?.runId,
          module4TaskStatus: initialOutcome.module4TaskStatus,
          module4TaskStartedAt: initialOutcome.module4TaskStartedAt,
          module4TaskFinishedAt: initialOutcome.module4TaskFinishedAt,
          module4TaskError: initialOutcome.module4Error,
          module4Exception: initialOutcome.module4Error,
        });
      } else {
        initialArtifacts = await loadComparisonArtifacts(
          sessionId,
          module1Result.dbRecordId,
          initialOutcome.module4Result,
          initialOutcome.module4Error,
        );
        if (initialArtifacts.comparisons.length > 0) {
          initialStep5Completed = true;
          await updateStepStatus(sessionId, 5, 'partial', '已完成首轮分析，等待检索完成后自动全量补跑');
          await updateResults(sessionId, {
            products: initialArtifacts.products,
            comparisons: initialArtifacts.comparisons,
            initialClaimCompareRunId: initialArtifacts.claimCompareRunId,
            claimCompareRunId: initialArtifacts.claimCompareRunId,
            resultsCompleteness: 'partial',
            step5Phase: 'initial',
            partialAnalysisAvailable: true,
            module4RunId: initialOutcome.module4Result?.runId,
            module4TaskStatus: initialOutcome.module4TaskStatus,
            module4TaskStartedAt: initialOutcome.module4TaskStartedAt,
            module4TaskFinishedAt: initialOutcome.module4TaskFinishedAt,
            module4TaskError: undefined,
            module4Exception: undefined,
          });
        } else {
          const initialMessage = initialArtifacts.errorMessage || '首轮分析未返回可用的比对结果';
          await updateStepStatus(sessionId, 5, 'error', initialMessage);
          await updateResults(sessionId, {
            initialClaimCompareRunId: initialArtifacts.claimCompareRunId,
            claimCompareRunId: initialArtifacts.claimCompareRunId,
            step5Phase: 'initial',
            partialAnalysisAvailable: false,
            module4RunId: initialOutcome.module4Result?.runId,
            module4TaskStatus: initialOutcome.module4TaskStatus,
            module4TaskStartedAt: initialOutcome.module4TaskStartedAt,
            module4TaskFinishedAt: initialOutcome.module4TaskFinishedAt,
            module4TaskError: initialMessage,
            module4Exception: initialMessage,
          });
        }
      }
    }

    while (!module3IsComplete && module3TaskStatus !== 'completed' && module3TaskStatus !== 'error' && module3TaskStatus !== 'cancelled' && module3TaskStatus !== 'timeout') {
      let runStatus: Module3RunStatusResult | null = null;
      try {
        runStatus = await getModule3RunStatus(module3Result.runId);
      } catch (statusError) {
        const statusErrorMessage = statusError instanceof Error ? statusError.message : String(statusError);
        console.warn(`[Pipeline ${sessionId}] 模块3状态查询失败: ${statusErrorMessage}`);
      }

      const latestSearchSnapshot = await getLatestSearchRunSnapshot(sessionId, module1Result.dbRecordId);
      if (latestSearchSnapshot) {
        module3ProductsCount = Math.max(module3ProductsCount, latestSearchSnapshot.total_products_count);
        module3IsComplete = module3IsComplete || latestSearchSnapshot.is_complete;
        if (!module3Result.searchRunId) {
          module3Result.searchRunId = latestSearchSnapshot.id;
        }
        if (!module3Error && latestSearchSnapshot.error_message) {
          module3Error = latestSearchSnapshot.error_message;
        }
      }
      if (runStatus) {
        module3TaskStatus = runStatus.status;
        if (runStatus.totalProductsCount > module3ProductsCount) {
          module3ProductsCount = runStatus.totalProductsCount;
        }
        if (typeof runStatus.isComplete === 'boolean') {
          module3IsComplete = runStatus.isComplete;
        } else if (runStatus.status === 'completed') {
          module3IsComplete = true;
        }
        if (runStatus.searchRunId && !module3Result.searchRunId) {
          module3Result.searchRunId = runStatus.searchRunId;
        }
        if (runStatus.errorMessage) {
          module3Error = runStatus.errorMessage;
        }
        if (runStatus.status === 'completed' || runStatus.status === 'error' || runStatus.status === 'cancelled' || runStatus.status === 'timeout') {
          module3TaskFinishedAt = module3TaskFinishedAt || new Date().toISOString();
        }
      }
      await updateResults(sessionId, {
        searchRunId: module3Result.searchRunId,
        module3RunId: module3Result.runId,
        module3TaskStatus,
        module3TaskStartedAt,
        module3TaskFinishedAt,
        module3TaskError: module3Error,
        module3Exception: module3Error,
      });
      if (module3IsComplete || module3TaskStatus === 'completed' || module3TaskStatus === 'error' || module3TaskStatus === 'cancelled' || module3TaskStatus === 'timeout') {
        break;
      }
      await sleep(MODULE3_POLL_INTERVAL_MS);
    }

    stepTimings['step4'] = Date.now() - step4Start;
    if (module3ProductsCount === 0) {
      const msg = module3Error || '步骤4未检索到任何商品，后台任务已结束';
      await updateStepStatus(sessionId, 4, 'error', msg);
      await updateSessionStatus(sessionId, 'error');
      await updateResults(sessionId, {
        step5Phase: initialStep5Triggered ? 'initial' : undefined,
      });
      return;
    }

    const step4TerminalMessage = module3TaskStatus && module3TaskStatus !== 'completed'
      ? `模块3最终状态为 ${module3TaskStatus}，将基于当前已检索到的 ${module3ProductsCount} 个商品继续分析`
      : initialStep5Completed
        ? '模块3已完成全部检索，开始执行全量补跑'
        : undefined;
    await updateStepStatus(sessionId, 4, 'completed', step4TerminalMessage);
    await updateResults(sessionId, {
      searchRunId: module3Result?.searchRunId,
      module3RunId: module3Result?.runId,
      module3TaskStatus,
      module3TaskStartedAt,
      module3TaskFinishedAt,
      module3TaskError: module3Error,
      module3Exception: module3Error,
    });
    console.log(
      `[Pipeline ${sessionId}] 步骤4完成 `
      + `(products=${module3ProductsCount}, complete=${module3IsComplete}, status=${module3TaskStatus || 'unknown'}, 耗时 ${stepTimings['step4']}ms)`,
    );

    // ========== 模块4（步骤5）：技术特征比对终轮补跑 ==========
    await updateStepStatus(sessionId, 5, 'running');
    console.log(`[Pipeline ${sessionId}] 步骤5: 执行全量补跑...`);
    const step5FinalStart = Date.now();
    const finalOutcome = await runModule4Task({
      sessionId,
      patentRecordId: module1Result.dbRecordId,
      runId: `${sessionId}-module4-final`,
      progressLabel: '模块4终轮进度',
    });
    stepTimings['step5_final'] = Date.now() - step5FinalStart;
    stepTimings['step5'] = (stepTimings['step5_initial'] || 0) + stepTimings['step5_final'];

    if (finalOutcome.module4RecoveredFromTransport) {
      console.warn(
        `[Pipeline ${sessionId}] 模块4终轮 HTTP 响应中断，但后台任务已完成并写入数据库 `
        + `(耗时 ${stepTimings['step5_final']}ms, claim_compare_run_id=${finalOutcome.module4Result?.claimCompareRunId || 'unknown'})`,
      );
    }

    let finalArtifacts: ComparisonArtifacts | null = null;
    let finalModule4Error = finalOutcome.module4Error;
    if (!finalModule4Error) {
      finalArtifacts = await loadComparisonArtifacts(
        sessionId,
        module1Result.dbRecordId,
        finalOutcome.module4Result,
        finalOutcome.module4Error,
      );
      if (finalArtifacts.comparisons.length === 0) {
        finalModule4Error = finalArtifacts.errorMessage || '终轮补跑未返回可用的比对结果';
      }
    }

    if (finalModule4Error) {
      const fallbackProducts = (initialArtifacts?.products && initialArtifacts.products.length > 0)
        ? initialArtifacts.products
        : await loadSearchProductsForSession(sessionId, module1Result.dbRecordId);
      await updateStepStatus(sessionId, 5, 'error', finalModule4Error);
      await updateStepStatus(sessionId, 6, 'error', `终轮补跑失败: ${finalModule4Error}`);
      await updateSessionStatus(sessionId, 'error');
      await updateResults(sessionId, {
        products: fallbackProducts,
        comparisons: initialArtifacts?.comparisons ?? [],
        claimCompareRunId: initialArtifacts?.claimCompareRunId,
        initialClaimCompareRunId: initialArtifacts?.claimCompareRunId,
        finalClaimCompareRunId: finalOutcome.module4Result?.claimCompareRunId,
        resultsCompleteness: initialArtifacts?.comparisons.length ? 'partial' : undefined,
        step5Phase: 'rerun',
        partialAnalysisAvailable: Boolean(initialArtifacts?.comparisons.length),
        module2Exception: module2Result?.exceptionType || module2Error,
        module3Exception: module3Error,
        module4RunId: finalOutcome.module4Result?.runId,
        module4TaskStatus: finalOutcome.module4TaskStatus,
        module4TaskStartedAt: finalOutcome.module4TaskStartedAt,
        module4TaskFinishedAt: finalOutcome.module4TaskFinishedAt,
        module4TaskError: finalModule4Error,
        module4Exception: finalModule4Error,
      });
      return;
    }

    await updateStepStatus(sessionId, 5, 'completed');
    await updateResults(sessionId, {
      products: finalArtifacts?.products ?? [],
      comparisons: finalArtifacts?.comparisons ?? [],
      claimCompareRunId: finalArtifacts?.claimCompareRunId,
      initialClaimCompareRunId: initialArtifacts?.claimCompareRunId,
      finalClaimCompareRunId: finalArtifacts?.claimCompareRunId,
      resultsCompleteness: 'final',
      step5Phase: 'completed',
      partialAnalysisAvailable: Boolean(initialArtifacts?.comparisons.length),
      module4RunId: finalOutcome.module4Result?.runId,
      module4TaskStatus: finalOutcome.module4TaskStatus,
      module4TaskStartedAt: finalOutcome.module4TaskStartedAt,
      module4TaskFinishedAt: finalOutcome.module4TaskFinishedAt,
      module4TaskError: undefined,
      module4Exception: undefined,
    });
    console.log(`[Pipeline ${sessionId}] 步骤5完成 (总耗时 ${stepTimings['step5']}ms)`);

    // ========== 步骤6：提取分析结果 ==========
    await updateStepStatus(sessionId, 6, 'running');
    const step6Start = Date.now();
    const patent: PatentInfo | undefined = patentFromModule1;
    const products: ProductInfo[] = finalArtifacts?.products ?? [];
    const comparisons: ProductComparison[] = finalArtifacts?.comparisons ?? [];

    if (comparisons.length === 0) {
      const msg = finalArtifacts?.errorMessage || '终轮补跑未返回可用的比对数据';
      stepTimings['step6'] = Date.now() - step6Start;
      await updateStepStatus(sessionId, 6, 'error', msg);
      await updateSessionStatus(sessionId, 'error');
      await updateResults(sessionId, {
        products: initialArtifacts?.products ?? [],
        comparisons: initialArtifacts?.comparisons ?? [],
        claimCompareRunId: initialArtifacts?.claimCompareRunId,
        initialClaimCompareRunId: initialArtifacts?.claimCompareRunId,
        finalClaimCompareRunId: finalArtifacts?.claimCompareRunId,
        resultsCompleteness: initialArtifacts?.comparisons.length ? 'partial' : undefined,
        step5Phase: 'rerun',
        partialAnalysisAvailable: Boolean(initialArtifacts?.comparisons.length),
        module2Exception: module2Result?.exceptionType || module2Error,
        module3Exception: module3Error,
        module4Exception: msg,
      });
      return;
    }

    stepTimings['step6'] = Date.now() - step6Start;
    await updateStepStatus(sessionId, 6, 'completed');

    // ========== 汇总完成 ==========
    const totalTime = Date.now() - pipelineStart;
    await updateSessionStatus(sessionId, 'completed');
    await updateResults(sessionId, {
      patent,
      products,
      comparisons,
      claimCompareRunId: finalArtifacts?.claimCompareRunId,
      initialClaimCompareRunId: initialArtifacts?.claimCompareRunId,
      finalClaimCompareRunId: finalArtifacts?.claimCompareRunId,
      resultsCompleteness: 'final',
      step5Phase: 'completed',
      partialAnalysisAvailable: Boolean(initialArtifacts?.comparisons.length),
      module2Exception: module2Result?.exceptionType || module2Error,
      module3Exception: module3Error,
      module4Exception: undefined,
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
