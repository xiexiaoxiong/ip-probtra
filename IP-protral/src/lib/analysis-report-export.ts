import ExcelJS from 'exceljs';
import type { PoolClient } from 'pg';
import { buildFallbackTokenUnits, computeClaimScores, computeProductScore } from '@/lib/claim-score';
import { PRODUCT_RISK_CONFIG, SCORE_BAND_CONFIG, scoreToBand, scoreToRiskLevel } from '@/lib/types';
import type { AnalysisSession, ClaimElementComparison, ProductComparison, ProductInfo } from '@/lib/types';

type JsonRecord = Record<string, unknown>;

interface KeywordRecordRow extends JsonRecord {
  keyword?: string;
}

interface SearchProductRow extends JsonRecord {
  id?: number;
  product_id?: string;
  product_name?: string;
  product_url?: string;
  product_source?: string;
  price?: string;
  brand?: string;
  manufacturer?: string;
  matched_keywords?: string;
  description?: string;
  picture?: string[] | string | null;
}

interface ClaimCompareRow extends JsonRecord {
  product_id?: string;
  product_name?: string;
  claim_id?: string;
  feature_id?: string;
  feature_text?: string;
  evidence?: string;
  comparison_result?: string;
  similarity_score?: number | string;
  score_band?: string;
  feature_full_score?: number | string;
  feature_awarded_score?: number | string;
  feature_effective_length?: number | string;
  matched_effective_length?: number | string;
  claim_total_effective_length?: number | string;
  zeroed_by_mismatch?: boolean | string;
  reason?: string;
  reasoning_type?: string;
  evidence_images?: string[] | string | null;
  token_units?: unknown;
}

interface ExportProduct {
  id: string;
  name: string;
  url?: string;
  source?: string;
  price?: string;
  brand?: string;
  manufacturer?: string;
  matchedKeywords?: string;
  description?: string;
  pictures: string[];
  comparison?: ProductComparison;
}

interface EmbeddedImage {
  buffer: Buffer;
  extension: 'jpeg' | 'png' | 'gif';
}

function toText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function parseStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
  }
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
      }
    } catch {
      return [value];
    }
  }
  return [];
}

function tableExists(
  client: Pick<PoolClient, 'query'>,
  tableName: string,
): Promise<boolean> {
  return client
    .query('select to_regclass($1) as exists', [tableName])
    .then((result) => Boolean(result.rows[0]?.exists));
}

async function selectAllIfExists(
  client: Pick<PoolClient, 'query'>,
  tableName: string,
  sql: string,
  params: unknown[] = [],
): Promise<JsonRecord[]> {
  if (!(await tableExists(client, tableName))) {
    return [];
  }
  return (await client.query(sql, params)).rows;
}

function normalizeScore(score: unknown, legacyStatus?: string): number {
  if (typeof score === 'number' && Number.isFinite(score)) {
    return Math.max(0, Math.min(100, Number(score.toFixed(2))));
  }
  if (typeof score === 'string' && score.trim()) {
    const parsed = Number(score.trim());
    if (Number.isFinite(parsed)) {
      return Math.max(0, Math.min(100, Number(parsed.toFixed(2))));
    }
  }
  const lower = (legacyStatus || '').toLowerCase();
  if (!lower) return 50;
  if (
    lower.includes('不匹配') ||
    lower.includes('not_match') ||
    lower.includes('not matching') ||
    lower.includes('不同') ||
    lower.includes('不一致') ||
    lower.includes('区别')
  ) {
    return 0;
  }
  if (
    lower.includes('匹配') ||
    lower.includes('match') ||
    lower.includes('matching') ||
    lower.includes('相同') ||
    lower.includes('一致') ||
    lower.includes('等同')
  ) {
    return 100;
  }
  return 50;
}

function riskLabel(comparison?: ProductComparison): string {
  if (!comparison) return '低风险候选';
  return PRODUCT_RISK_CONFIG[comparison.riskLevel].label;
}

function scoreLabel(element: ClaimElementComparison): string {
  const fullScore = element.scoreDetail?.fullScore ?? element.similarityScore;
  return `${element.similarityScore.toFixed(2)} 分 / ${fullScore.toFixed(2)} 分`;
}

function getVerdictStats(comparison?: ProductComparison): { exactMatch: number; exactMismatch: number; uncertain: number } {
  const elements = comparison?.claimElements || [];
  return {
    // 这里必须按 scoreBand 统计，不能再按分数区间统计：
    // score=0 既可能是“明确不相同”，也可能是“待确认（信息不足）”。
    exactMatch: elements.filter((item) => item.scoreBand === 'exact_match').length,
    exactMismatch: elements.filter((item) => item.scoreBand === 'exact_mismatch').length,
    uncertain: elements.filter((item) => item.scoreBand === 'uncertain').length,
  };
}

function mapComparisonRows(rows: ClaimCompareRow[]): Map<string, ProductComparison> {
  const comparisonMap = new Map<string, ProductComparison>();

  for (const row of rows) {
    const productId = toText(row.product_id);
    const productName = toText(row.product_name);
    const key = productId || productName;
    if (!key) continue;

    if (!comparisonMap.has(key)) {
      comparisonMap.set(key, {
        productId: key,
        productName: productName || key,
        productSimilarityScore: 0,
        productScoreBand: 'exact_mismatch',
        riskLevel: 'clear_low_risk',
        claimElements: [],
        claimScores: [],
      });
    }

    const comparison = comparisonMap.get(key)!;
    const similarityScore = normalizeScore(row.similarity_score, toText(row.comparison_result));
    const matchedEffectiveLength = normalizeScore(row.matched_effective_length, undefined) || 0;
    const totalEffectiveLength = normalizeScore(row.feature_effective_length, undefined) || 0;
    const zeroedByMismatch = normalizeBoolean(row.zeroed_by_mismatch) || false;
    comparison.claimElements.push({
      featureId: toText(row.feature_id) || undefined,
      claimElement: toText(row.feature_text),
      productFeature: toText(row.evidence),
      similarityScore,
      scoreBand: scoreToBand(similarityScore, {
        zeroedByMismatch,
        matchedEffectiveLength,
        totalEffectiveLength,
      }),
      reasoning: [toText(row.reason), toText(row.reasoning_type)].filter(Boolean).join(' | '),
      patentReference: toText(row.claim_id) || undefined,
      evidenceImages: parseStringArray(row.evidence_images),
      scoreRationale: toText(row.reason) || undefined,
      tokenUnits: parseTokenUnits(row.token_units, toText(row.feature_text)),
      scoreDetail: {
        fullScore: normalizeScore(row.feature_full_score, undefined),
        awardedScore: normalizeScore(row.feature_awarded_score, undefined) || similarityScore,
        effectiveLength: totalEffectiveLength,
        matchedEffectiveLength,
        zeroedByMismatch,
      },
      isLegacyScore: row.token_units == null && row.feature_full_score == null,
    });
  }

  for (const comparison of comparisonMap.values()) {
    comparison.claimScores = computeClaimScores(comparison.claimElements);
    const weighted = computeProductScore(comparison.claimScores);
    comparison.productSimilarityScore = weighted.productSimilarityScore;
    comparison.productScoreBand = weighted.productScoreBand;
    comparison.riskLevel = weighted.riskLevel;
    comparison.highestScoringClaimId = weighted.highestScoringClaimId;
    comparison.isLegacyScore = comparison.claimElements.every((item) => item.isLegacyScore);
  }

  return comparisonMap;
}

function normalizeBoolean(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    if (value === 'true') return true;
    if (value === 'false') return false;
  }
  return undefined;
}

function parseTokenUnits(value: unknown, featureText: string) {
  if (Array.isArray(value)) {
    return value
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => ({
        text: toText(item.text),
        normalizedText: toText(item.normalized_text ?? item.normalizedText) || undefined,
        status: normalizeUnitStatus(toText(item.status ?? item.unit_status)),
        evidence: toText(item.evidence) || undefined,
        reason: toText(item.reason) || undefined,
        start: typeof item.start === 'number' ? item.start : undefined,
        end: typeof item.end === 'number' ? item.end : undefined,
        effectiveLength: typeof item.effective_length === 'number' ? item.effective_length : undefined,
      }))
      .filter((item) => item.text);
  }
  return buildFallbackTokenUnits(featureText);
}

function normalizeUnitStatus(status: string): 'match' | 'mismatch' | 'uncertain' {
  const lower = status.trim().toLowerCase();
  if (['match', 'matching', '相同', '明确相同'].includes(lower)) return 'match';
  if (['mismatch', 'not_match', '不同', '不相同', '明确不相同'].includes(lower)) return 'mismatch';
  return 'uncertain';
}

function mapSessionComparisons(session: AnalysisSession): Map<string, ProductComparison> {
  const comparisonMap = new Map<string, ProductComparison>();
  for (const comparison of session.results?.comparisons || []) {
    const key = comparison.productId || comparison.productName;
    if (key) {
      comparisonMap.set(key, comparison);
    }
  }
  return comparisonMap;
}

function mapProductRows(rows: SearchProductRow[]): Map<string, ExportProduct> {
  const productMap = new Map<string, ExportProduct>();

  for (const row of rows) {
    const rowId = row.id != null ? toText(row.id) : '';
    const productId = toText(row.product_id);
    const productName = toText(row.product_name);
    const key = rowId || productId || productName;
    if (!key) continue;

    productMap.set(key, {
      id: key,
      name: productName || key,
      url: toText(row.product_url) || undefined,
      source: toText(row.product_source) || undefined,
      price: toText(row.price) || undefined,
      brand: toText(row.brand) || undefined,
      manufacturer: toText(row.manufacturer) || undefined,
      matchedKeywords: toText(row.matched_keywords) || undefined,
      description: toText(row.description) || undefined,
      pictures: parseStringArray(row.picture),
    });
  }

  return productMap;
}

function mergeSessionProducts(
  productMap: Map<string, ExportProduct>,
  sessionProducts: ProductInfo[] | undefined,
  comparisonMap: Map<string, ProductComparison>,
): ExportProduct[] {
  const merged = new Map(productMap);

  for (const product of sessionProducts || []) {
    const key = product.id || product.name;
    if (!key) continue;
    const current = merged.get(key);
    merged.set(key, {
      id: key,
      name: current?.name || product.name || key,
      url: current?.url || product.url,
      source: current?.source || product.source,
      price: current?.price || product.price,
      brand: current?.brand,
      manufacturer: current?.manufacturer,
      matchedKeywords: current?.matchedKeywords,
      description: current?.description || product.description,
      pictures: current?.pictures?.length ? current.pictures : product.imageUrl ? [product.imageUrl] : [],
      comparison: current?.comparison,
    });
  }

  for (const [key, comparison] of comparisonMap) {
    if (!merged.has(key)) {
      merged.set(key, {
        id: key,
        name: comparison.productName || key,
        pictures: [],
        comparison,
      });
      continue;
    }

    const current = merged.get(key)!;
    current.comparison = current.comparison || comparison;
    current.name = current.name || comparison.productName || key;
  }

  return Array.from(merged.values());
}

function makeSafeSheetName(rawName: string, fallback: string, usedNames: Set<string>): string {
  const base = (rawName || fallback).replace(/[\\/*?:[\]]/g, ' ').trim() || fallback;
  const compact = base.replace(/\s+/g, ' ');
  let candidate = compact.slice(0, 31) || fallback;
  let index = 1;

  while (usedNames.has(candidate)) {
    const suffix = `_${index}`;
    candidate = `${compact.slice(0, Math.max(1, 31 - suffix.length))}${suffix}`;
    index += 1;
  }

  usedNames.add(candidate);
  return candidate;
}

function getImageExtension(contentType: string | null, buffer: Buffer): EmbeddedImage['extension'] | null {
  const normalized = (contentType || '').toLowerCase();
  if (normalized.includes('png')) return 'png';
  if (normalized.includes('jpeg') || normalized.includes('jpg')) return 'jpeg';
  if (normalized.includes('gif')) return 'gif';

  if (buffer.length >= 4) {
    if (buffer[0] === 0x89 && buffer[1] === 0x50 && buffer[2] === 0x4e && buffer[3] === 0x47) return 'png';
    if (buffer[0] === 0xff && buffer[1] === 0xd8) return 'jpeg';
    if (buffer[0] === 0x47 && buffer[1] === 0x49 && buffer[2] === 0x46) return 'gif';
  }

  return null;
}

class ImageFetcher {
  private readonly cache = new Map<string, Promise<EmbeddedImage | null>>();

  get(url: string): Promise<EmbeddedImage | null> {
    if (!this.cache.has(url)) {
      this.cache.set(url, this.fetch(url));
    }
    return this.cache.get(url)!;
  }

  private async fetch(url: string): Promise<EmbeddedImage | null> {
    if (!url || !/^https?:\/\//i.test(url)) {
      return null;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    try {
      const response = await fetch(url, {
        signal: controller.signal,
        headers: {
          'user-agent': 'IP-protral-export/1.0',
        },
      });

      if (!response.ok) {
        return null;
      }

      const arrayBuffer = await response.arrayBuffer();
      const buffer = Buffer.from(arrayBuffer);
      const extension = getImageExtension(response.headers.get('content-type'), buffer);
      if (!extension) {
        return null;
      }

      return { buffer, extension };
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }
}

function setBorder(row: ExcelJS.Row): void {
  row.eachCell((cell) => {
    cell.border = {
      top: { style: 'thin', color: { argb: 'FFD9D9D9' } },
      left: { style: 'thin', color: { argb: 'FFD9D9D9' } },
      bottom: { style: 'thin', color: { argb: 'FFD9D9D9' } },
      right: { style: 'thin', color: { argb: 'FFD9D9D9' } },
    };
    cell.alignment = { vertical: 'top', wrapText: true };
  });
}

async function addImageToCell(
  workbook: ExcelJS.Workbook,
  worksheet: ExcelJS.Worksheet,
  imageFetcher: ImageFetcher,
  imageUrl: string | undefined,
  column: number,
  row: number,
  width: number,
  height: number,
): Promise<void> {
  if (!imageUrl) return;
  const image = await imageFetcher.get(imageUrl);
  if (!image) return;

  const imageId = workbook.addImage({
    base64: image.buffer.toString('base64'),
    extension: image.extension,
  });

  worksheet.addImage(imageId, {
    tl: { col: column - 1 + 0.08, row: row - 1 + 0.1 },
    ext: { width, height },
  });
}

function addSummaryHeader(summarySheet: ExcelJS.Worksheet, session: AnalysisSession, keywords: string[]): number {
  summarySheet.mergeCells('A1:M1');
  summarySheet.getCell('A1').value = '专利检索与比对导出报告';
  summarySheet.getCell('A1').font = { bold: true, size: 16 };
  summarySheet.getCell('A1').alignment = { vertical: 'middle' };

  summarySheet.getCell('A2').value = '分析会话';
  summarySheet.getCell('B2').value = session.id;
  summarySheet.getCell('D2').value = '导出时间';
  summarySheet.getCell('E2').value = new Date().toLocaleString('zh-CN', { hour12: false });

  summarySheet.getCell('A3').value = '专利标题';
  summarySheet.getCell('B3').value = session.results?.patent?.title || session.patentTitle || '-';
  summarySheet.getCell('D3').value = '专利号';
  summarySheet.getCell('E3').value = session.results?.patent?.patentNumber || session.patentNumber || '-';

  summarySheet.getCell('A4').value = '检索关键词';
  summarySheet.mergeCells('B4:M4');
  summarySheet.getCell('B4').value = keywords.length > 0 ? keywords.join('、') : '—';
  summarySheet.getRow(4).height = 28;

  return 6;
}

export async function buildAnalysisReportWorkbook(
  client: Pick<PoolClient, 'query'>,
  session: AnalysisSession,
): Promise<ExcelJS.Workbook> {
  const sessionId = session.id;
  const resultIds = session.results || {};

  let patentRecordId =
    typeof resultIds.dbRecordId === 'number' && resultIds.dbRecordId > 0
      ? resultIds.dbRecordId
      : undefined;

  const keywordRuns = await selectAllIfExists(
    client,
    'keyword_runs',
    'select * from keyword_runs where analysis_session_id = $1 order by id desc',
    [sessionId],
  );
  const searchRuns = await selectAllIfExists(
    client,
    'search_runs',
    'select * from search_runs where analysis_session_id = $1 order by id desc',
    [sessionId],
  );
  const claimCompareRuns = await selectAllIfExists(
    client,
    'claim_compare_runs',
    'select * from claim_compare_runs where analysis_session_id = $1 order by id desc',
    [sessionId],
  );

  if (!patentRecordId) {
    patentRecordId = [keywordRuns[0], searchRuns[0], claimCompareRuns[0]]
      .map((row) => Number(row?.patent_record_id || 0))
      .find((value) => value > 0);
  }

  const keywordRunIds = keywordRuns.map((row) => Number(row.id)).filter((value) => value > 0);
  const searchRunIds = searchRuns.map((row) => Number(row.id)).filter((value) => value > 0);
  const claimCompareRunIds = claimCompareRuns.map((row) => Number(row.id)).filter((value) => value > 0);

  const keywordRows = keywordRunIds.length > 0
    ? await selectAllIfExists(
        client,
        'keyword_records',
        'select * from keyword_records where keyword_run_id = any($1::int[]) order by id asc',
        [keywordRunIds],
      )
    : [];

  const searchProductRows = searchRunIds.length > 0
    ? await selectAllIfExists(
        client,
        'search_products',
        'select * from search_products where search_run_id = any($1::int[]) order by id asc',
        [searchRunIds],
      )
    : patentRecordId
      ? await selectAllIfExists(
          client,
          'search_products',
          'select * from search_products where patent_record_id = $1 order by id asc',
          [patentRecordId],
        )
      : [];

  const claimCompareRows = claimCompareRunIds.length > 0
    ? await selectAllIfExists(
        client,
        'claim_compare_results',
        'select * from claim_compare_results where claim_compare_run_id = any($1::int[]) order by id asc',
        [claimCompareRunIds],
      )
    : patentRecordId
      ? await selectAllIfExists(
          client,
          'claim_compare_results',
          'select * from claim_compare_results where patent_record_id = $1 order by id asc',
          [patentRecordId],
        )
      : [];

  const keywords = (keywordRows as KeywordRecordRow[])
    .map((row) => toText(row.keyword))
    .filter(Boolean);

  const comparisonMap = mapComparisonRows(claimCompareRows as ClaimCompareRow[]);
  const sessionComparisonMap = mapSessionComparisons(session);
  for (const [key, comparison] of sessionComparisonMap) {
    if (!comparisonMap.has(key)) {
      comparisonMap.set(key, comparison);
    }
  }

  const productMap = mapProductRows(searchProductRows as SearchProductRow[]);
  const products = mergeSessionProducts(
    productMap,
    session.results?.products,
    comparisonMap,
  ).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));

  for (const product of products) {
    if (!product.comparison) {
      product.comparison = comparisonMap.get(product.id) || comparisonMap.get(product.name);
    }
  }

  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'IP-protral';
  workbook.company = 'IP-protral';
  workbook.created = new Date();
  workbook.modified = new Date();

  const summarySheet = workbook.addWorksheet('结果概览', {
    views: [{ state: 'frozen', ySplit: 6 }],
  });
  summarySheet.columns = [
    { width: 8 },
    { width: 16 },
    { width: 28 },
    { width: 12 },
    { width: 12 },
    { width: 12 },
    { width: 16 },
    { width: 18 },
    { width: 14 },
    { width: 12 },
    { width: 12 },
    { width: 12 },
    { width: 14 },
  ];

  const usedSheetNames = new Set<string>(['结果概览']);
  const sheetNameMap = new Map<string, string>();
  products.forEach((product, index) => {
    const sheetName = makeSafeSheetName(
      `${index + 1}_${product.name}`,
      `商品${index + 1}`,
      usedSheetNames,
    );
    sheetNameMap.set(product.id, sheetName);
  });

  const imageFetcher = new ImageFetcher();
  const headerRowNumber = addSummaryHeader(summarySheet, session, keywords.length > 0 ? keywords : session.results?.keywords || []);
  const headerRow = summarySheet.getRow(headerRowNumber);
  headerRow.values = [
    '序号',
    '商品ID',
    '商品名称',
    '来源',
    '价格',
    '品牌',
    '主图',
    '商品链接',
    '风险标签',
    '商品总分',
    '100分特征',
    '待确认特征',
    '详情页',
  ];
  headerRow.font = { bold: true };
  headerRow.fill = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: { argb: 'FFF4F4F5' },
  };
  setBorder(headerRow);

  let summaryDataRow = headerRowNumber + 1;
  for (const [index, product] of products.entries()) {
    const stats = getVerdictStats(product.comparison);
    const row = summarySheet.getRow(summaryDataRow);
    row.values = [
      index + 1,
      product.id,
      product.name,
      product.source || '—',
      product.price || '—',
      product.brand || '—',
      '',
      product.url
        ? { text: '打开商品链接', hyperlink: product.url, tooltip: product.url }
        : '—',
      riskLabel(product.comparison),
      product.comparison?.productSimilarityScore ?? '—',
      stats.exactMatch,
      stats.uncertain,
      {
        text: '查看比对表',
        hyperlink: `#'${sheetNameMap.get(product.id)}'!A1`,
        tooltip: '跳转到商品比对工作表',
      },
    ];
    row.height = 60;
    setBorder(row);
    await addImageToCell(workbook, summarySheet, imageFetcher, product.pictures[0], 7, summaryDataRow, 64, 48);
    summaryDataRow += 1;
  }

  for (const product of products) {
    const sheetName = sheetNameMap.get(product.id) || product.name;
    const sheet = workbook.addWorksheet(sheetName);
    sheet.columns = [
      { width: 12 },
      { width: 18 },
      { width: 28 },
      { width: 28 },
      { width: 14 },
      { width: 36 },
      { width: 16 },
      { width: 34 },
    ];

    sheet.mergeCells('A1:H1');
    sheet.getCell('A1').value = `${product.name} 比对表`;
    sheet.getCell('A1').font = { bold: true, size: 15 };
    sheet.getCell('A1').alignment = { vertical: 'middle' };

    sheet.getCell('A2').value = '返回概览';
    sheet.getCell('A2').value = {
      text: '返回概览',
      hyperlink: `#'结果概览'!A1`,
      tooltip: '返回概览工作表',
    };
    sheet.getCell('B2').value = '商品ID';
    sheet.getCell('C2').value = product.id;
    sheet.getCell('E2').value = '风险标签';
    sheet.getCell('F2').value = riskLabel(product.comparison);

    sheet.getCell('B3').value = '商品来源';
    sheet.getCell('C3').value = product.source || '—';
    sheet.getCell('E3').value = '商品总分';
    sheet.getCell('F3').value = product.comparison?.productSimilarityScore ?? '—';

    sheet.getCell('B4').value = '品牌';
    sheet.getCell('C4').value = product.brand || '—';
    sheet.getCell('E4').value = '最高权利要求分';
    sheet.getCell('F4').value = product.comparison?.claimScores.reduce((max, item) => Math.max(max, item.similarityScore), 0) ?? '—';

    sheet.getCell('B5').value = '商品链接';
    sheet.getCell('C5').value = product.url
      ? { text: product.url, hyperlink: product.url, tooltip: product.url }
      : '—';
    sheet.mergeCells('C5:H5');

    sheet.getCell('B6').value = '商品描述';
    sheet.getCell('C6').value = product.description || '—';
    sheet.mergeCells('C6:H7');
    sheet.getRow(6).height = 28;
    sheet.getRow(7).height = 28;

    sheet.getCell('A9').value = '商品图片';
    sheet.getCell('A9').font = { bold: true };
    sheet.mergeCells('B9:H9');
    sheet.getCell('B9').value = product.pictures.length > 0 ? product.pictures.join('\n') : '—';
    sheet.getRow(9).height = 42;

    if (product.pictures.length > 0) {
      sheet.getRow(10).height = 84;
      sheet.getRow(11).height = 84;
      const previewImages = product.pictures.slice(0, 4);
      for (const [imageIndex, imageUrl] of previewImages.entries()) {
        const targetColumn = imageIndex < 2 ? 2 + imageIndex * 2 : 2 + (imageIndex - 2) * 2;
        const targetRow = imageIndex < 2 ? 10 : 11;
        await addImageToCell(workbook, sheet, imageFetcher, imageUrl, targetColumn, targetRow, 92, 72);
      }
    }

    const tableHeaderRowNumber = product.pictures.length > 0 ? 13 : 11;
    const tableHeaderRow = sheet.getRow(tableHeaderRowNumber);
    tableHeaderRow.values = [
      '特征编号',
      '权利要求',
      '特征内容',
      '商品特征',
        '相似度',
        '命中比例',
      '比对分析',
      '证据图片',
      '证据图片链接',
    ];
    tableHeaderRow.font = { bold: true };
    tableHeaderRow.fill = {
      type: 'pattern',
      pattern: 'solid',
      fgColor: { argb: 'FFF4F4F5' },
    };
    setBorder(tableHeaderRow);

    const claimElements = product.comparison?.claimElements || [];
    let currentRowNumber = tableHeaderRowNumber + 1;

    if (claimElements.length === 0) {
      const row = sheet.getRow(currentRowNumber);
      row.values = ['—', '—', '未找到该商品的数据库比对结果', '—', '—', '—', '—', '—', '—'];
      setBorder(row);
      currentRowNumber += 1;
    } else {
      for (const element of claimElements) {
        const row = sheet.getRow(currentRowNumber);
        const evidenceImages = element.evidenceImages || [];
        row.values = [
          element.featureId || '—',
          element.patentReference || '—',
          element.claimElement || '—',
          element.productFeature || '—',
          scoreLabel(element),
          element.scoreDetail
            ? `${element.scoreDetail.matchedEffectiveLength}/${element.scoreDetail.effectiveLength}`
            : '—',
          element.reasoning || '—',
          '',
          evidenceImages.length > 0 ? evidenceImages.join('\n') : '—',
        ];
        row.height = evidenceImages.length > 0 ? 80 : 44;
        setBorder(row);
        await addImageToCell(workbook, sheet, imageFetcher, evidenceImages[0], 7, currentRowNumber, 72, 56);
        currentRowNumber += 1;
      }
    }

    sheet.views = [{ state: 'frozen', ySplit: tableHeaderRowNumber }];
  }

  return workbook;
}
