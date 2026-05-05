/**
 * 下载商品图片到本地
 * 
 * 用法: npx tsx scripts/download-product-images.ts
 * 
 * 输出: .data/product-images/{product_id}/{image_hash}.jpg
 * 同时更新数据库中的 picture 和 evidence_images 字段为本地路径
 */

import 'dotenv/config';
import path from 'path';
import { config } from 'dotenv';
import { mkdir, writeFile, readFile } from 'fs/promises';
import { existsSync } from 'fs';
import crypto from 'crypto';

config({ path: path.resolve(process.cwd(), '.env.local') });

import { pgQuery } from '@/lib/postgres';

const IMAGES_DIR = path.resolve(process.cwd(), '.data/product-images');

function getUrlHash(url: string): string {
  return crypto.createHash('md5').update(url).digest('hex').slice(0, 12);
}

async function downloadImage(url: string, destPath: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if (!response.ok) {
      console.log(`  ⚠️ 下载失败: ${response.status} ${url.slice(0, 60)}...`);
      return false;
    }
    const buffer = Buffer.from(await response.arrayBuffer());
    await writeFile(destPath, buffer);
    return true;
  } catch (error) {
    console.log(`  ⚠️ 下载异常: ${error instanceof Error ? error.message : String(error)}`);
    return false;
  }
}

async function main() {
  const patentRecordId = 26;
  
  console.log('='.repeat(60));
  console.log('  下载商品图片到本地');
  console.log('='.repeat(60));

  // 1. 获取所有商品图片 URL
  const productRows = await pgQuery(`
    SELECT product_id, product_name, picture
    FROM search_products 
    WHERE patent_record_id = $1
    ORDER BY product_id
  `, [patentRecordId]);

  // 构建 URL → 本地路径的映射
  const urlToLocalMap = new Map<string, string>();
  let totalUrls = 0;
  let downloaded = 0;
  let skipped = 0;

  for (const row of productRows.rows) {
    const productId = row.product_id as string;
    const productName = row.product_name as string;
    const pictureField = row.picture;
    
    let urls: string[] = [];
    if (typeof pictureField === 'string') {
      urls = pictureField.split(',').map(u => u.trim()).filter(Boolean);
    } else if (Array.isArray(pictureField)) {
      urls = pictureField.filter((u): u is string => typeof u === 'string' && u.trim().length > 0);
    }
    
    if (urls.length === 0) continue;

    const productDir = path.join(IMAGES_DIR, productId);
    await mkdir(productDir, { recursive: true });

    console.log(`\n商品 ${productId}: ${productName.slice(0, 40)}... (${urls.length} 张图片)`);

    for (const url of urls) {
      totalUrls++;
      const hash = getUrlHash(url);
      const localPath = path.join(productDir, `${hash}.jpg`);
      const relativePath = path.relative(path.resolve(process.cwd(), '.data'), localPath);
      
      urlToLocalMap.set(url, relativePath);

      if (existsSync(localPath)) {
        skipped++;
        continue;
      }

      const success = await downloadImage(url, localPath);
      if (success) {
        downloaded++;
        console.log(`  ✅ ${hash}.jpg`);
      } else {
        console.log(`  ❌ ${hash}.jpg`);
      }
    }
  }

  console.log(`\n下载完成: ${downloaded} 新下载, ${skipped} 已存在, 共 ${totalUrls} 个 URL`);

  // 2. 获取最新的 claim_compare_run_id
  const runIdResult = await pgQuery('SELECT MAX(id) as max_id FROM claim_compare_runs');
  const latestRunId = Number(runIdResult.rows[0]?.max_id || 0);
  
  if (latestRunId === 0) {
    console.log('无比对结果，跳过 evidence_images 更新');
    return;
  }

  // 3. 更新 evidence_images 为本地路径
  console.log('\n更新 evidence_images 字段为本地路径...');
  const compareRows = await pgQuery(`
    SELECT id, evidence_images
    FROM claim_compare_results 
    WHERE claim_compare_run_id = $1 AND evidence_images IS NOT NULL
  `, [latestRunId]);

  let updatedCount = 0;
  for (const row of compareRows.rows) {
    const id = row.id as number;
    const evidenceImages = row.evidence_images as string[] | null;
    
    if (!Array.isArray(evidenceImages) || evidenceImages.length === 0) continue;

    const localImages: string[] = [];
    for (const url of evidenceImages) {
      const localPath = urlToLocalMap.get(url);
      if (localPath) {
        localImages.push(localPath);
      }
    }

    if (localImages.length > 0) {
      await pgQuery(
        'UPDATE claim_compare_results SET evidence_images = $1 WHERE id = $2',
        [JSON.stringify(localImages), id]
      );
      updatedCount++;
    }
  }

  console.log(`已更新 ${updatedCount} 条记录的 evidence_images 字段`);

  // 4. 保存 URL 映射文件供 HTML 生成使用
  const mappingPath = path.resolve(process.cwd(), '.data/image-url-mapping.json');
  await writeFile(mappingPath, JSON.stringify(Object.fromEntries(urlToLocalMap), null, 2));
  console.log(`URL 映射已保存到: ${mappingPath}`);
}

main().catch(console.error).finally(() => process.exit(0));
