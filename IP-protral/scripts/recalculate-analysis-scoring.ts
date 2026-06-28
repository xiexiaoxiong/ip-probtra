import 'dotenv/config';
import { config as loadEnv } from 'dotenv';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import pg from 'pg';

const envLocal = resolve(process.cwd(), '.env.local');
if (existsSync(envLocal)) {
  loadEnv({ path: envLocal, override: false });
}

const SESSION_ID = process.argv[2] || 'analysis_1782625905937_titb69';
const databaseUrl = process.env.PGDATABASE_URL || process.env.DATABASE_URL;

if (!databaseUrl) {
  throw new Error('Missing PGDATABASE_URL or DATABASE_URL');
}

type Row = {
  id: number;
  claim_compare_run_id: number;
  product_id: string | null;
  product_name: string | null;
  claim_id: string | null;
  feature_id: string | null;
  similarity_score: number | null;
  score_band: string | null;
  feature_full_score: number | null;
  feature_awarded_score: number | null;
  feature_effective_length: number | null;
  matched_effective_length: number | null;
  claim_total_effective_length: number | null;
  zeroed_by_mismatch: boolean | null;
  raw_payload: Record<string, unknown> | null;
};

type Recalculated = {
  featureScore: number;
  roundedFeatureScore: number;
  featureWeight: number;
  contribution: number;
  cappedMatched: number;
  band: string;
};

function round2(value: number): number {
  return Number(value.toFixed(2));
}

function capMatched(effectiveLength: number, matchedLength: number): number {
  if (effectiveLength <= 0 || matchedLength <= 0) return 0;
  return Math.min(Math.trunc(matchedLength), Math.trunc(effectiveLength));
}

function scoreBand(score: number, zeroedByMismatch: boolean, english = false): string {
  if (zeroedByMismatch) return english ? 'exact_mismatch' : '明确不相同';
  if (score >= 100) return english ? 'exact_match' : '明确相同';
  if (score > 70) return english ? 'high_similarity' : '明确相同';
  if (score >= 30) return english ? 'medium_similarity' : '中等相似';
  return english ? 'low_similarity' : '低相似';
}

function riskLevel(score: number, zeroedByMismatch: boolean): string {
  if (zeroedByMismatch) return 'clear_low_risk';
  if (score > 70) return 'high_risk';
  if (score >= 30) return 'medium_risk';
  return 'low_risk';
}

function recalculateFeature(row: Row): Recalculated {
  const effectiveLength = Number(row.feature_effective_length || 0);
  const claimTotal = Number(row.claim_total_effective_length || 0);
  const cappedMatched = capMatched(effectiveLength, Number(row.matched_effective_length || 0));
  const zeroedByMismatch = Boolean(row.zeroed_by_mismatch);
  const featureWeight = effectiveLength > 0 && claimTotal > 0
    ? round2((effectiveLength / claimTotal) * 100)
    : round2(Number(row.feature_full_score || 0));
  const featureScore = zeroedByMismatch || effectiveLength <= 0
    ? 0
    : round2((cappedMatched / effectiveLength) * 100);
  const contribution = zeroedByMismatch ? 0 : round2(featureScore * featureWeight / 100);
  return {
    featureScore,
    roundedFeatureScore: Math.round(featureScore),
    featureWeight,
    contribution,
    cappedMatched,
    band: scoreBand(featureScore, zeroedByMismatch),
  };
}

function withUpdatedRawPayload(row: Row, calc: Recalculated): Record<string, unknown> | null {
  if (!row.raw_payload || typeof row.raw_payload !== 'object') return row.raw_payload;
  return {
    ...row.raw_payload,
    similarity_score: calc.featureScore,
    score_band: calc.band,
    feature_full_score: calc.featureWeight,
    feature_awarded_score: calc.contribution,
    matched_effective_length: calc.cappedMatched,
    score_rationale: `特征占比 ${calc.featureWeight}%，特征得分 ${calc.featureScore}%，加权贡献 ${calc.contribution}，命中有效长度 ${calc.cappedMatched}/${row.feature_effective_length || 0}`,
  };
}

function groupKey(row: Pick<Row, 'claim_compare_run_id' | 'product_id' | 'product_name'>): string {
  return `${row.claim_compare_run_id}::${row.product_id || row.product_name || ''}`;
}

function claimKey(row: Pick<Row, 'claim_id'>): string {
  return row.claim_id || 'unknown';
}

async function main() {
  const pool = new pg.Pool({ connectionString: databaseUrl });
  const client = await pool.connect();
  try {
    await client.query('begin');

    const rowsResult = await client.query<Row>(
      `select id, claim_compare_run_id, product_id, product_name, claim_id, feature_id,
              similarity_score, score_band, feature_full_score, feature_awarded_score,
              feature_effective_length, matched_effective_length, claim_total_effective_length,
              zeroed_by_mismatch, raw_payload
         from claim_compare_results
        where analysis_session_id = $1
        order by claim_compare_run_id, product_id nulls last, product_name nulls last, claim_id, feature_id, id`,
      [SESSION_ID],
    );

    if (rowsResult.rowCount === 0) {
      throw new Error(`No claim_compare_results found for ${SESSION_ID}`);
    }

    const recalculatedById = new Map<number, Recalculated>();
    for (const row of rowsResult.rows) {
      const calc = recalculateFeature(row);
      recalculatedById.set(row.id, calc);
      await client.query(
        `update claim_compare_results
            set similarity_score = $2,
                score_band = $3,
                feature_full_score = $4,
                feature_awarded_score = $5,
                matched_effective_length = $6,
                raw_payload = $7
          where id = $1`,
        [
          row.id,
          calc.roundedFeatureScore,
          calc.band,
          calc.featureWeight,
          calc.contribution,
          calc.cappedMatched,
          withUpdatedRawPayload(row, calc),
        ],
      );
    }

    const runGroups = new Map<number, Map<string, Row[]>>();
    for (const row of rowsResult.rows) {
      if (!runGroups.has(row.claim_compare_run_id)) runGroups.set(row.claim_compare_run_id, new Map());
      const products = runGroups.get(row.claim_compare_run_id)!;
      const key = groupKey(row);
      if (!products.has(key)) products.set(key, []);
      products.get(key)!.push(row);
    }

    const finalScoresByProduct = new Map<string, number>();
    for (const [runId, products] of runGroups.entries()) {
      const productScores: number[] = [];
      for (const productRows of products.values()) {
        const zeroed = productRows.some((row) => Boolean(row.zeroed_by_mismatch));
        let productScore = 0;
        if (!zeroed) {
          const claimScores = new Map<string, number>();
          for (const row of productRows) {
            const calc = recalculatedById.get(row.id)!;
            const key = claimKey(row);
            claimScores.set(key, round2((claimScores.get(key) || 0) + calc.contribution));
          }
          productScore = round2(Math.min([...claimScores.values()].reduce((sum, score) => sum + score, 0), 100));
        }
        productScores.push(productScore);
        finalScoresByProduct.set(`${rowProductId(productRows[0])}`, productScore);
      }

      const summary = {
        total_products: productScores.length,
        average_score: productScores.length ? round2(productScores.reduce((sum, score) => sum + score, 0) / productScores.length) : 0,
        max_score: productScores.length ? round2(Math.max(...productScores)) : 0,
        min_score: productScores.length ? round2(Math.min(...productScores)) : 0,
        scoring_rule: 'feature_score_times_feature_weight_with_capped_matched_length',
        recalculated_at: new Date().toISOString(),
      };
      await client.query(
        `update claim_compare_runs set result_summary = $2, updated_at = now() where id = $1`,
        [runId, summary],
      );
    }

    const sessionResult = await client.query<{ results: Record<string, unknown> | null }>(
      'select results from analysis_sessions where id = $1 for update',
      [SESSION_ID],
    );
    if (sessionResult.rowCount) {
      const results = sessionResult.rows[0].results || {};
      const comparisons = Array.isArray(results.comparisons) ? results.comparisons : [];
      const updatedComparisons = comparisons.map((comparison) => updateComparison(comparison));
      await client.query(
        `update analysis_sessions set results = $2, updated_at = now() where id = $1`,
        [SESSION_ID, { ...results, comparisons: updatedComparisons }],
      );
    }

    await client.query('commit');

    console.table(
      [...finalScoresByProduct.entries()]
        .filter(([key]) => key)
        .map(([product, score]) => ({ product, score }))
        .sort((a, b) => b.score - a.score)
        .slice(0, 20),
    );
    console.log(`Recalculated ${rowsResult.rowCount} feature rows for ${SESSION_ID}`);
  } catch (error) {
    await client.query('rollback');
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
}

function rowProductId(row: Row): string {
  return row.product_id || row.product_name || '';
}

function updateComparison(value: unknown): unknown {
  if (!value || typeof value !== 'object') return value;
  const comparison = value as Record<string, unknown>;
  const elements = Array.isArray(comparison.claimElements) ? comparison.claimElements : [];
  const updatedElements = elements.map((element) => {
    if (!element || typeof element !== 'object') return element;
    const item = element as Record<string, unknown>;
    const detail = item.scoreDetail && typeof item.scoreDetail === 'object' ? item.scoreDetail as Record<string, unknown> : {};
    const effectiveLength = Number(detail.effectiveLength || 0);
    const matchedLength = capMatched(effectiveLength, Number(detail.matchedEffectiveLength || 0));
    const zeroed = Boolean(detail.zeroedByMismatch);
    const featureScore = zeroed || effectiveLength <= 0 ? 0 : round2((matchedLength / effectiveLength) * 100);
    const updatedDetail = {
      ...detail,
      matchedEffectiveLength: matchedLength,
      awardedScore: Number(detail.fullScore || 0) > 0 ? round2(featureScore * Number(detail.fullScore || 0) / 100) : Number(detail.awardedScore || 0),
      zeroedByMismatch: zeroed,
    };
    return {
      ...item,
      similarityScore: featureScore,
      scoreBand: scoreBand(featureScore, zeroed, true),
      scoreDetail: updatedDetail,
    };
  });

  const claimMap = new Map<string, Record<string, unknown>[]>();
  for (const element of updatedElements) {
    if (!element || typeof element !== 'object') continue;
    const item = element as Record<string, unknown>;
    const key = String(item.patentReference || 'unknown');
    if (!claimMap.has(key)) claimMap.set(key, []);
    claimMap.get(key)!.push(item);
  }

  const claimScores = [...claimMap.entries()].map(([id, items]) => {
    const zeroed = items.some((item) => Boolean((item.scoreDetail as Record<string, unknown> | undefined)?.zeroedByMismatch));
    const total = items.reduce((sum, item) => sum + Number((item.scoreDetail as Record<string, unknown> | undefined)?.effectiveLength || 0), 0);
    const matched = items.reduce((sum, item) => {
      const detail = item.scoreDetail as Record<string, unknown> | undefined;
      return sum + Number(detail?.matchedEffectiveLength || 0);
    }, 0);
    const score = zeroed ? 0 : round2(Math.min(items.reduce((sum, item) => sum + Number((item.scoreDetail as Record<string, unknown> | undefined)?.awardedScore || 0), 0), 100));
    return {
      claimId: id,
      similarityScore: score,
      scoreBand: scoreBand(score, zeroed, true),
      claimTotalEffectiveLength: total,
      claimMatchedEffectiveLength: matched,
      zeroedByMismatch: zeroed,
    };
  });

  const zeroed = claimScores.some((item) => item.zeroedByMismatch);
  const productScore = zeroed ? 0 : round2(Math.min(claimScores.reduce((sum, item) => sum + item.similarityScore, 0), 100));
  return {
    ...comparison,
    claimElements: updatedElements,
    claimScores,
    productSimilarityScore: productScore,
    productScoreBand: scoreBand(productScore, zeroed, true),
    riskLevel: riskLevel(productScore, zeroed),
    highestScoringClaimId: claimScores.slice().sort((a, b) => b.similarityScore - a.similarityScore)[0]?.claimId,
  };
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
