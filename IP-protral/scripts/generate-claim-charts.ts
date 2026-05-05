/**
 * 生成 Claim Chart 表格（每个商品一个表格）
 * 
 * 用法: npx tsx scripts/generate-claim-charts.ts
 * 
 * 基于 session: analysis_1777910597406_1lhxrc
 */

import 'dotenv/config';
import path from 'path';
import { config } from 'dotenv';

config({ path: path.resolve(process.cwd(), '.env.local') });

import { pgQuery } from '@/lib/postgres';

interface ComparisonRow {
  id: number;
  product_id: string;
  product_name: string;
  feature_text: string;
  comparison_result: string;
  reason: string;
}

function resultToLabel(result: string): string {
  const upper = result.toUpperCase();
  if (upper === 'MATCH') return '相同/等同';
  if (upper === 'NO_MATCH') return '不相同';
  return '不确定';
}

function resultToEmoji(result: string): string {
  const upper = result.toUpperCase();
  if (upper === 'MATCH') return '✅';
  if (upper === 'NO_MATCH') return '❌';
  return '⚠️';
}

async function main() {
  const claimCompareRunId = 3;

  console.log('='.repeat(80));
  console.log('  Claim Chart 比对表生成');
  console.log('='.repeat(80));

  try {
    const rows = await pgQuery<ComparisonRow>(`
      SELECT id, product_id, product_name, feature_text, comparison_result, reason
      FROM claim_compare_results 
      WHERE claim_compare_run_id = $1 
      ORDER BY product_id, id ASC
    `, [claimCompareRunId]);

    if (rows.rows.length === 0) {
      console.log('\n无比对结果');
      return;
    }

    // 按商品分组
    const productMap = new Map<string, ComparisonRow[]>();
    for (const row of rows.rows) {
      const key = row.product_id;
      if (!productMap.has(key)) {
        productMap.set(key, []);
      }
      productMap.get(key)!.push(row);
    }

    let productIndex = 0;
    for (const [productId, comparisons] of productMap.entries()) {
      productIndex++;
      const productName = comparisons[0]?.product_name || productId;
      
      // 统计
      const matchCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'MATCH').length;
      const noMatchCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'NO_MATCH').length;
      const uncertainCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'UNCERTAIN').length;

      console.log('\n' + '='.repeat(80));
      console.log(`  商品 ${productIndex}: ${productName}`);
      console.log(`  产品ID: ${productId}`);
      console.log(`  统计: ${matchCount} 相同/等同 | ${noMatchCount} 不相同 | ${uncertainCount} 不确定 | 共 ${comparisons.length} 个特征`);
      console.log('='.repeat(80));

      // 表头
      console.log('\n┌─────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬──────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐');
      console.log('│ 序号 │ 专利权利要求特征                                                                                                                                          │ 商品技术特征                                                                                                                                                  │ 比对结论     │ 分析依据                                                                                                                                                                                                                         │');
      console.log('├─────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤');

      let seq = 0;
      for (const comp of comparisons) {
        seq++;
        const label = resultToLabel(comp.comparison_result || '');
        const emoji = resultToEmoji(comp.comparison_result || '');
        
        // 简化输出格式（避免终端过宽）
        console.log(`\n  ${seq}. ${emoji} ${label}`);
        console.log(`     专利特征: ${comp.feature_text?.slice(0, 100)}${comp.feature_text && comp.feature_text.length > 100 ? '...' : ''}`);
        console.log(`     分析依据: ${comp.reason?.slice(0, 150)}${comp.reason && comp.reason.length > 150 ? '...' : ''}`);
      }

      console.log('\n');
    }

    console.log('='.repeat(80));
    console.log(`  共生成 ${productMap.size} 个商品的 Claim Chart`);
    console.log('='.repeat(80));

  } catch (error) {
    console.error('生成失败:', error);
  } finally {
    process.exit(0);
  }
}

main();
