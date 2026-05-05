/**
 * 查询数据库中的比对结果
 * 
 * 用法: npx tsx scripts/query-db-results.ts
 * 
 * 基于 session: analysis_1777910597406_1lhxrc
 * - claimCompareRunId: 3
 * - dbRecordId: 26
 */

import 'dotenv/config';
import path from 'path';
import { config } from 'dotenv';

// Load .env.local
config({ path: path.resolve(process.cwd(), '.env.local') });

import { pgQuery } from '@/lib/postgres';

async function main() {
  const claimCompareRunId = 3;
  const patentRecordId = 26;
  const sessionId = 'analysis_1777910597406_1lhxrc';

  console.log('='.repeat(60));
  console.log('  查询数据库中的比对结果');
  console.log('='.repeat(60));
  console.log(`\nSession: ${sessionId}`);
  console.log(`claimCompareRunId: ${claimCompareRunId}`);
  console.log(`patentRecordId: ${patentRecordId}\n`);

  try {
    // 1. 查询 claim_compare_runs
    console.log('-'.repeat(60));
    console.log('1. claim_compare_runs (比对运行记录)');
    console.log('-'.repeat(60));
    const runRows = await pgQuery(`
      SELECT * FROM claim_compare_runs 
      WHERE patent_record_id = $1 OR analysis_session_id = $2
      ORDER BY id DESC
    `, [patentRecordId, sessionId]);
    
    if (runRows.rows.length === 0) {
      console.log('  无记录');
    } else {
      console.log(JSON.stringify(runRows.rows, null, 2));
    }

    // 2. 查询 claim_compare_results (比对结果详情)
    console.log('\n' + '-'.repeat(60));
    console.log(`2. claim_compare_results (claim_compare_run_id = ${claimCompareRunId})`);
    console.log('-'.repeat(60));
    const resultRows = await pgQuery(`
      SELECT * FROM claim_compare_results 
      WHERE claim_compare_run_id = $1 
      ORDER BY id ASC
    `, [claimCompareRunId]);
    
    if (resultRows.rows.length === 0) {
      console.log('  无记录');
    } else {
      console.log(`\n  共 ${resultRows.rows.length} 条记录\n`);
      for (const row of resultRows.rows) {
        console.log(`  [ID: ${row.id}]`);
        console.log(`    product_id: ${row.product_id}`);
        console.log(`    product_name: ${row.product_name}`);
        console.log(`    feature_text: ${(row.feature_text || '').slice(0, 80)}...`);
        console.log(`    comparison_result: ${row.comparison_result}`);
        console.log(`    reason: ${(row.reason || '').slice(0, 100)}...`);
        console.log('');
      }
    }

    // 3. 查询 search_products (商品信息)
    console.log('-'.repeat(60));
    console.log('3. search_products (商品详情)');
    console.log('-'.repeat(60));
    const productRows = await pgQuery(`
      SELECT product_id, product_name, product_url, product_source, price, brand, description
      FROM search_products 
      WHERE patent_record_id = $1 
      ORDER BY id ASC
      LIMIT 10
    `, [patentRecordId]);
    
    if (productRows.rows.length === 0) {
      console.log('  无记录');
    } else {
      console.log(`\n  共 ${productRows.rows.length} 条记录 (显示前10条)\n`);
      for (const row of productRows.rows) {
        console.log(`  [${row.product_id}] ${row.product_name}`);
        console.log(`    url: ${row.product_url || 'N/A'}`);
        console.log(`    price: ${row.price || 'N/A'}`);
        console.log(`    brand: ${row.brand || 'N/A'}`);
        console.log(`    description: ${(row.description || '').slice(0, 80)}...`);
        console.log('');
      }
    }

    // 4. 查询 search_runs (搜索运行记录)
    console.log('-'.repeat(60));
    console.log('4. search_runs (搜索运行记录)');
    console.log('-'.repeat(60));
    const searchRunRows = await pgQuery(`
      SELECT id, total_products_count, is_complete, error_message, created_at
      FROM search_runs 
      WHERE patent_record_id = $1 OR analysis_session_id = $2
      ORDER BY id DESC
      LIMIT 5
    `, [patentRecordId, sessionId]);
    
    if (searchRunRows.rows.length === 0) {
      console.log('  无记录');
    } else {
      console.log(JSON.stringify(searchRunRows.rows, null, 2));
    }

  } catch (error) {
    console.error('查询失败:', error);
  } finally {
    process.exit(0);
  }
}

main();
