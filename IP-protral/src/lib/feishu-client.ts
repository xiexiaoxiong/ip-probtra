// ============================================================
// 飞书多维表格 API 客户端
// 用于从飞书多维表格读取工作流的分析结果数据
// ============================================================

const FEISHU_API_BASE = 'https://open.feishu.cn/open-apis';

/** 飞书 API 配置 */
function getFeishuConfig() {
  return {
    appId: process.env.FEISHU_APP_ID || '',
    appSecret: process.env.FEISHU_APP_SECRET || '',
  };
}

// tenant_access_token 缓存
let cachedToken: string | null = null;
let tokenExpiry = 0;

/** 清除 token 缓存（切换凭证时使用） */
export function clearTokenCache() {
  cachedToken = null;
  tokenExpiry = 0;
}

/** 获取飞书 tenant_access_token */
async function getTenantAccessToken(): Promise<string> {
  // 检查缓存
  if (cachedToken && Date.now() < tokenExpiry) {
    return cachedToken;
  }

  const config = getFeishuConfig();
  if (!config.appId || !config.appSecret) {
    throw new Error('缺少飞书 API 凭证：请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量。在飞书开放平台 (open.feishu.cn) 创建企业自建应用，获取 App ID 和 App Secret，并添加"多维表格"相关权限。');
  }

  const response = await fetch(`${FEISHU_API_BASE}/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      app_id: config.appId,
      app_secret: config.appSecret,
    }),
  });

  const data = await response.json();

  if (data.code !== 0) {
    throw new Error(`获取飞书 tenant_access_token 失败: ${data.msg}`);
  }

  cachedToken = data.tenant_access_token;
  // 提前5分钟过期，避免边界情况
  tokenExpiry = Date.now() + (data.expire - 300) * 1000;

  return cachedToken!;
}

/** 飞书 API 请求 */
async function feishuRequest(path: string, method: string = 'GET', body?: unknown) {
  const token = await getTenantAccessToken();

  const options: RequestInit = {
    method,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  };

  if (body && method !== 'GET') {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${FEISHU_API_BASE}${path}`, options);
  const data = await response.json();

  if (data.code !== 0) {
    throw new Error(`飞书 API 错误 (${data.code}): ${data.msg}`);
  }

  return data;
}

/** 从飞书URL中提取 app_token */
export function extractAppToken(feishuUrl: string): string {
  // URL 格式: https://xxx.feishu.cn/base/{app_token}
  // 或: https://xxx.feishu.cn/wiki/{page_id} (wiki内嵌)
  const baseMatch = feishuUrl.match(/\/base\/([A-Za-z0-9]+)/);
  if (baseMatch) return baseMatch[1];

  const wikiMatch = feishuUrl.match(/\/wiki\/([A-Za-z0-9]+)/);
  if (wikiMatch) return wikiMatch[1];

  // 如果直接就是 app_token
  if (/^[A-Za-z0-9]{20,}$/.test(feishuUrl)) return feishuUrl;

  throw new Error(`无法从飞书URL提取 app_token: ${feishuUrl}`);
}

/** 多维表格信息 */
interface BitableTable {
  table_id: string;
  name: string;
}

/** 获取多维表格的所有数据表 */
export async function listTables(appToken: string): Promise<BitableTable[]> {
  const data = await feishuRequest(`/bitable/v1/apps/${appToken}/tables`);
  return data.data?.items || [];
}

/** 读取数据表的所有记录 */
export async function listRecords(
  appToken: string,
  tableId: string,
  pageSize: number = 100,
): Promise<Array<Record<string, unknown>>> {
  const allRecords: Array<Record<string, unknown>> = [];
  let pageToken: string | undefined;

  do {
    const params = new URLSearchParams({
      page_size: String(pageSize),
    });
    if (pageToken) params.set('page_token', pageToken);

    const data = await feishuRequest(
      `/bitable/v1/apps/${appToken}/tables/${tableId}/records?${params.toString()}`,
    );

    const items = data.data?.items || [];
    for (const item of items) {
      if (item.fields) {
        allRecords.push(item.fields as Record<string, unknown>);
      }
    }

    pageToken = data.data?.has_more ? data.data?.page_token : undefined;
  } while (pageToken);

  return allRecords;
}

/** 从飞书多维表格读取所有分析结果 */
export async function readAnalysisResults(
  feishuUrl: string,
  appTokenOverride?: string,
): Promise<{
  tables: Record<string, Array<Record<string, unknown>>>;
  appToken: string;
}> {
  const appToken = appTokenOverride || extractAppToken(feishuUrl);

  // 1. 列出所有数据表
  const tables = await listTables(appToken);

  if (tables.length === 0) {
    throw new Error(`飞书多维表格中没有数据表 (app_token: ${appToken})`);
  }

  // 2. 并行读取每个数据表的记录（优化：多个表格同时读取）
  const entries = await Promise.allSettled(
    tables.map(async (table) => {
      try {
        const records = await listRecords(appToken, table.table_id);
        return [table.name, records] as [string, Array<Record<string, unknown>>];
      } catch (error) {
        console.warn(`[FeishuClient] 读取数据表 ${table.name} 失败:`, error);
        return [table.name, []] as [string, Array<Record<string, unknown>>];
      }
    }),
  );

  const result: Record<string, Array<Record<string, unknown>>> = {};
  for (const entry of entries) {
    if (entry.status === 'fulfilled') {
      result[entry.value[0]] = entry.value[1];
    }
  }

  return { tables: result, appToken };
}

/** 快速读取飞书表格中指定表的记录（用于行业判断等轻量读取场景） */
export async function readTableRecords(
  feishuUrl: string,
  tableNameHint: string,
  appTokenOverride?: string,
): Promise<Array<Record<string, unknown>>> {
  const appToken = appTokenOverride || extractAppToken(feishuUrl);
  const tables = await listTables(appToken);

  // 找到匹配的数据表
  const targetTable = tables.find(t =>
    t.name.toLowerCase().includes(tableNameHint.toLowerCase()),
  );

  if (!targetTable) {
    // 找不到精确匹配，尝试第一个表
    if (tables.length > 0) {
      return listRecords(appToken, tables[0].table_id);
    }
    return [];
  }

  return listRecords(appToken, targetTable.table_id);
}

// ============================================================
// 数据转换：将飞书表格记录映射为业务模型
// ============================================================

import { buildFallbackTokenUnits, computeClaimScores, computeProductScore } from './claim-score';
import { scoreToBand, scoreToRiskLevel } from './types';
import type { PatentInfo, ProductInfo, ProductComparison } from './types';

/** 从飞书记录中提取文本字段 */
function getTextField(record: Record<string, unknown>, ...fields: string[]): string {
  for (const field of fields) {
    const value = record[field];
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      const text = value
        .map((item: unknown) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'text' in item) return (item as { text: string }).text;
          return '';
        })
        .join('');
      if (text) return text;
    }
    if (value && typeof value === 'object' && 'text' in value) {
      const text = (value as { text: string }).text || '';
      if (text) return text;
    }
  }
  return '';
}

/** 从飞书记录中提取链接字段 */
function getLinkField(record: Record<string, unknown>, field: string): string {
  const value = record[field];
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const link = value.find((item: unknown) => {
      if (item && typeof item === 'object' && 'link' in item) return true;
      return typeof item === 'string' && item.startsWith('http');
    });
    if (link && typeof link === 'object' && 'link' in link) return (link as { link: string }).link;
    if (typeof link === 'string') return link;
  }
  if (value && typeof value === 'object' && 'link' in value) {
    return (value as { link: string }).link || '';
  }
  // 可能是 URL 字段
  const text = getTextField(record, field);
  if (text.startsWith('http')) return text;
  return '';
}

/** 从飞书记录中提取数字字段 */
function getNumberField(record: Record<string, unknown>, ...fields: string[]): number | undefined {
  for (const field of fields) {
    const value = record[field];
    if (typeof value === 'number') return value;
    if (typeof value === 'string') {
      const num = parseFloat(value);
      if (!isNaN(num)) return num;
    }
  }
  return undefined;
}

/** 从飞书表格数据映射为 PatentInfo */
export function mapPatentInfo(
  records: Array<Record<string, unknown>>,
): PatentInfo {
  if (records.length === 0) return {};

  const first = records[0];
  return {
    title: getTextField(first, '专利标题') || getTextField(first, '标题') || getTextField(first, 'title'),
    patentNumber: getTextField(first, '专利号') || getTextField(first, '申请号') || getTextField(first, 'patent_number'),
    independentClaims: extractListField(first, '独立权利要求') || extractListField(first, 'independent_claims'),
    dependentClaims: extractListField(first, '从属权利要求') || extractListField(first, 'dependent_claims'),
    specification: getTextField(first, '说明书') || getTextField(first, '摘要') || getTextField(first, 'specification'),
    drawings: extractListField(first, '附图') || extractListField(first, 'drawings'),
  };
}

/** 从飞书记录中提取列表字段 */
function extractListField(record: Record<string, unknown>, field: string): string[] | undefined {
  const value = record[field];
  if (Array.isArray(value)) {
    return value.map((item: unknown) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object' && 'text' in item) return (item as { text: string }).text;
      return String(item);
    }).filter(Boolean);
  }
  const text = getTextField(record, field);
  if (text) return text.split('\n').map(s => s.trim()).filter(Boolean);
  return undefined;
}

/** 从飞书表格数据映射为 ProductInfo[] */
export function mapProducts(
  records: Array<Record<string, unknown>>,
): ProductInfo[] {
  return records.map((record, index) => ({
    id: getTextField(record, '商品ID') || getTextField(record, 'id') || `product_${index + 1}`,
    name: getTextField(record, '商品名称') || getTextField(record, '产品名称') || getTextField(record, 'name') || `商品 ${index + 1}`,
    url: getLinkField(record, '商品链接') || getLinkField(record, '链接') || getLinkField(record, 'url'),
    imageUrl: getLinkField(record, '图片') || getLinkField(record, 'image_url'),
    description: getTextField(record, '商品描述') || getTextField(record, '描述') || getTextField(record, 'description'),
    source: getTextField(record, '来源') || getTextField(record, '平台') || getTextField(record, 'source'),
    price: getTextField(record, '价格') || getTextField(record, 'price') || (getNumberField(record, '价格')?.toString()),
  }));
}

/** 从飞书表格数据映射为 ProductComparison[] */
export function mapComparisons(
  records: Array<Record<string, unknown>>,
): ProductComparison[] {
  // 按商品分组
  const productMap = new Map<string, {
    productId: string;
    productName: string;
    elements: ProductComparison['claimElements'];
    claimScores: ProductComparison['claimScores'];
  }>();

  for (const record of records) {
    const productId = getTextField(record, '商品ID') || getTextField(record, 'product_id') || 'unknown';
    const productName = getTextField(record, '商品名称') || getTextField(record, 'product_name') || productId;

    if (!productMap.has(productId)) {
      productMap.set(productId, { productId, productName, elements: [], claimScores: [] });
    }

    const group = productMap.get(productId)!;
    const similarityScore = getNumberField(record, '相似度分数') ?? getNumberField(record, 'similarity_score')
      ?? legacyStatusToScore(getTextField(record, '匹配状态') || getTextField(record, '比对结果') || getTextField(record, 'status'));
    const feishuMatched = getNumberField(record, 'matched_effective_length') ?? 0;
    const feishuTotal = getNumberField(record, 'feature_effective_length') ?? 0;
    const feishuZeroed = getBooleanField(record, 'zeroed_by_mismatch');
    group.elements.push({
      featureId: getTextField(record, '特征编号') || getTextField(record, 'feature_id') || undefined,
      claimElement: getTextField(record, '权利要求特征') || getTextField(record, '专利技术特征') || getTextField(record, 'claim_element'),
      productFeature: getTextField(record, '商品特征') || getTextField(record, '产品技术特征') || getTextField(record, 'product_feature'),
      similarityScore,
      scoreBand: normalizeScoreBand(getTextField(record, '分段标签') || getTextField(record, 'score_band'))
        || scoreToBand(similarityScore, {
          zeroedByMismatch: feishuZeroed,
          matchedEffectiveLength: feishuMatched,
          totalEffectiveLength: feishuTotal,
        }),
      reasoning: getTextField(record, '推理过程') || getTextField(record, '比对分析') || getTextField(record, 'reasoning'),
      patentReference: getTextField(record, 'claim_id') || getTextField(record, '专利原文') || getTextField(record, 'patent_reference') || undefined,
      productReference: getTextField(record, '商品原文') || getTextField(record, 'product_reference') || undefined,
      evidenceImages: extractListField(record, 'evidence_images') || undefined,
      scoreRationale: getTextField(record, 'score_rationale') || undefined,
      tokenUnits: parseTokenUnits(record['token_units'], getTextField(record, '权利要求特征') || getTextField(record, 'claim_element')),
      scoreDetail: {
        fullScore: getNumberField(record, 'feature_full_score') ?? similarityScore,
        awardedScore: getNumberField(record, 'feature_awarded_score') ?? similarityScore,
        effectiveLength: feishuTotal,
        matchedEffectiveLength: feishuMatched,
        zeroedByMismatch: feishuZeroed,
      },
      isLegacyScore: !('token_units' in record) && !('feature_full_score' in record),
    });
  }

  return Array.from(productMap.values()).map((group) => {
    const claimScores = computeClaimScores(group.elements);
    const weighted = computeProductScore(claimScores);
    return {
      productId: group.productId,
      productName: group.productName,
      productSimilarityScore: weighted.productSimilarityScore,
      productScoreBand: weighted.productScoreBand,
      riskLevel: weighted.riskLevel,
      claimElements: group.elements,
      claimScores,
      highestScoringClaimId: weighted.highestScoringClaimId,
      isLegacyScore: group.elements.every((item) => item.isLegacyScore),
      ruleApplied: undefined,
    };
  });
}

function legacyStatusToScore(status: string): number {
  const lower = status.toLowerCase();
  if (!lower) return 50;
  if (lower.includes('不匹配') || lower.includes('not_match') || lower.includes('不同') || lower.includes('不一致')) {
    return 0;
  }
  if (lower.includes('匹配') || lower.includes('match') || lower.includes('相同') || lower.includes('一致') || lower.includes('等同')) {
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
        text: getTextField(item, 'text'),
        normalizedText: getTextField(item, 'normalized_text', 'normalizedText') || undefined,
        status: normalizeUnitStatus(getTextField(item, 'status', 'unit_status')),
        evidence: getTextField(item, 'evidence') || undefined,
        reason: getTextField(item, 'reason') || undefined,
        start: getNumberField(item, 'start'),
        end: getNumberField(item, 'end'),
        effectiveLength: getNumberField(item, 'effective_length', 'effectiveLength'),
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

/** 判断飞书 API 凭证是否已配置 */
export function isFeishuConfigured(): boolean {
  const config = getFeishuConfig();
  return !!(config.appId && config.appSecret);
}
