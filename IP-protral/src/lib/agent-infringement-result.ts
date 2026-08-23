import type {
  ProductComparison,
  ProductInfo,
  ProductRiskLevel,
} from './types';

type JsonObject = Record<string, unknown>;

export type AgentInfringementRiskCounts = Record<ProductRiskLevel, number>;

export type AgentInfringementResult = {
  status: string;
  isPartial: boolean;
  headline: string;
  detail: string;
  patentLabel: string;
  evidenceBoundary: string;
  keywordCount: number;
  productCount: number;
  analyzedProductCount: number;
  riskCounts: AgentInfringementRiskCounts;
  products: ProductInfo[];
  comparisons: ProductComparison[];
};

const TERMINAL_STATUSES = new Set(['completed', 'error', 'failed']);
const PRODUCT_RISK_LEVELS = new Set<ProductRiskLevel>([
  'high_risk',
  'medium_risk',
  'low_risk',
  'clear_low_risk',
]);

function objectValue(value: unknown): JsonObject | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function stringValue(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function productRows(value: unknown): ProductInfo[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item, index) => {
    const row = objectValue(item);
    if (!row) return [];
    const id = stringValue(row.id) || `product-${index + 1}`;
    const name = stringValue(row.name) || stringValue(row.productName) || `商品 ${index + 1}`;
    return [{
      ...row,
      id,
      name,
      url: stringValue(row.url) || undefined,
      imageUrl: stringValue(row.imageUrl) || undefined,
      description: stringValue(row.description) || undefined,
      source: stringValue(row.source) || undefined,
      price: stringValue(row.price) || undefined,
      brand: stringValue(row.brand) || undefined,
      manufacturer: stringValue(row.manufacturer) || undefined,
      company: stringValue(row.company) || undefined,
      updatedAt: stringValue(row.updatedAt) || undefined,
    } as ProductInfo];
  });
}

function comparisonRows(value: unknown): ProductComparison[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const row = objectValue(item);
    if (!row) return [];
    const productId = stringValue(row.productId);
    const productName = stringValue(row.productName);
    if (!productId && !productName) return [];
    const riskLevel = PRODUCT_RISK_LEVELS.has(row.riskLevel as ProductRiskLevel)
      ? row.riskLevel as ProductRiskLevel
      : 'low_risk';
    return [{
      ...row,
      productId: productId || productName,
      productName: productName || productId,
      productSimilarityScore: Number(row.productSimilarityScore || 0),
      productScoreBand: row.productScoreBand || 'low_similarity',
      riskLevel,
      claimElements: Array.isArray(row.claimElements) ? row.claimElements : [],
      claimScores: Array.isArray(row.claimScores) ? row.claimScores : [],
    } as ProductComparison];
  });
}

/**
 * Builds the final Agent projection from the owner-scoped infringement session.
 * It never recomputes scores or changes the persisted comparison facts.
 */
export function buildAgentInfringementResult(value: unknown): AgentInfringementResult | null {
  const payload = objectValue(value);
  const session = objectValue(payload?.session);
  if (!session || stringValue(session.analysisKind) !== 'infringement') return null;

  const status = stringValue(session.status).toLowerCase();
  if (!TERMINAL_STATUSES.has(status)) return null;

  const results = objectValue(session.results);
  const products = productRows(results?.products);
  const comparisons = comparisonRows(results?.comparisons);
  if (status !== 'completed' && products.length === 0 && comparisons.length === 0) return null;

  const patent = objectValue(results?.patent);
  const patentNumber = stringValue(patent?.patentNumber) || stringValue(session.patentNumber);
  const patentTitle = stringValue(patent?.title) || stringValue(session.patentTitle);
  const patentLabel = [
    patentNumber,
    patentTitle ? `《${patentTitle}》` : '',
  ].filter(Boolean).join('') || '当前专利';
  const keywords = Array.isArray(results?.keywords)
    ? results.keywords.map(stringValue).filter(Boolean)
    : [];
  const riskCounts: AgentInfringementRiskCounts = {
    high_risk: 0,
    medium_risk: 0,
    low_risk: 0,
    clear_low_risk: 0,
  };
  for (const comparison of comparisons) riskCounts[comparison.riskLevel] += 1;

  const completeness = stringValue(results?.resultsCompleteness).toLowerCase();
  const isPartial = status !== 'completed'
    || completeness === 'partial'
    || comparisons.length < products.length;
  const headline = isPartial
    ? '本次侵权分析已收口，以下为当前可用结果'
    : '本次专利侵权分析已经完成';
  const detail = comparisons.length
    ? `共检索到 ${products.length} 件商品，完成 ${comparisons.length} 件商品的独立权利要求技术特征比对；下表按商品总分从高到低展示。`
    : `本次检索到 ${products.length} 件商品，但没有形成可展示的逐商品技术特征比对。`;

  return {
    status,
    isPartial,
    headline,
    detail,
    patentLabel,
    evidenceBoundary: '分数和风险标签仅表示当前商品资料与权利要求技术特征的对齐结果，不能替代律师结合完整证据作出的侵权法律判断。',
    keywordCount: keywords.length,
    productCount: products.length,
    analyzedProductCount: comparisons.length,
    riskCounts,
    products,
    comparisons,
  };
}
