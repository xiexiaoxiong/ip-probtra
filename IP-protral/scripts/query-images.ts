/**
 * 查询数据库中的图片字段
 */

import 'dotenv/config';
import path from 'path';
import { config } from 'dotenv';

config({ path: path.resolve(process.cwd(), '.env.local') });

import { pgQuery } from '@/lib/postgres';

async function main() {
  const claimCompareRunId = 3;
  const patentRecordId = 26;

  console.log('='.repeat(60));
  console.log('  查询图片相关字段');
  console.log('='.repeat(60));

  try {
    // 1. 查看 claim_compare_results 表的所有列
    console.log('\n1. claim_compare_results 表结构');
    console.log('-'.repeat(60));
    const columns = await pgQuery(`
      SELECT column_name, data_type 
      FROM information_schema.columns 
      WHERE table_name = 'claim_compare_results'
      ORDER BY ordinal_position
    `, []);
    console.log(JSON.stringify(columns.rows, null, 2));

    // 2. 查看 claim_compare_results 的一条完整记录
    console.log('\n2. claim_compare_results 完整记录示例');
    console.log('-'.repeat(60));
    const sampleRow = await pgQuery(`
      SELECT * FROM claim_compare_results 
      WHERE claim_compare_run_id = $1 
      LIMIT 1
    `, [claimCompareRunId]);
    if (sampleRow.rows.length > 0) {
      console.log(JSON.stringify(sampleRow.rows[0], null, 2));
    }

    // 3. 查看 search_products 的 picture 字段
    console.log('\n3. search_products 的 picture 字段示例');
    console.log('-'.repeat(60));
    const pictures = await pgQuery(`
      SELECT product_id, product_name, picture 
      FROM search_products 
      WHERE patent_record_id = $1 
      LIMIT 5
    `, [patentRecordId]);
    for (const row of pictures.rows) {
      console.log(`\n[${row.product_id}] ${row.product_name}`);
      console.log(`  picture 类型: ${typeof row.picture}`);
      console.log(`  picture 内容: ${String(row.picture).slice(0, 500)}`);
    }

    // 4. 查看 claim_compare_runs 表结构
    console.log('\n4. claim_compare_runs 表结构');
    console.log('-'.repeat(60));
    const runColumns = await pgQuery(`
      SELECT column_name, data_type 
      FROM information_schema.columns 
      WHERE table_name = 'claim_compare_runs'
      ORDER BY ordinal_position
    `, []);
    console.log(JSON.stringify(runColumns.rows, null, 2));

  } catch (error) {
    console.error('查询失败:', error);
  } finally {
    process.exit(0);
  }
}

main();
