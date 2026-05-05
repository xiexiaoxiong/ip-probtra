/**
 * 生成 Claim Chart HTML 报告（每个商品一个独立页面）
 * 
 * 用法: npx tsx scripts/export-claim-charts-html.ts
 * 输出: 
 *   - .data/claim-charts/index.html (索引页)
 *   - .data/claim-charts/product-{id}.html (每个商品独立页面)
 */

import 'dotenv/config';
import path from 'path';
import { config } from 'dotenv';
import { mkdir, writeFile, readFile } from 'fs/promises';

config({ path: path.resolve(process.cwd(), '.env.local') });

import { pgQuery } from '@/lib/postgres';

const DATA_DIR = path.resolve(process.cwd(), '.data');
const OUTPUT_DIR = path.join(DATA_DIR, 'claim-charts');

interface ComparisonRow {
  id: number;
  product_id: string;
  product_name: string;
  feature_id: string;
  feature_text: string;
  evidence: string;
  comparison_result: string;
  reason: string;
  evidence_images: string[] | null;
}

function resultToConfig(result: string): { label: string; color: string; bg: string; icon: string } {
  const upper = result?.toUpperCase() || '';
  if (upper === 'MATCH') return { label: '相同/等同', color: '#dc2626', bg: '#fef2f2', icon: '✅' };
  if (upper === 'NO_MATCH') return { label: '不相同', color: '#16a34a', bg: '#f0fdf4', icon: '❌' };
  return { label: '不确定', color: '#d97706', bg: '#fffbeb', icon: '⚠️' };
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function parsePictures(pictureField: unknown): string[] {
  if (!pictureField) return [];
  let urls: string[] = [];
  if (typeof pictureField === 'string') {
    urls = pictureField.split(',').map(u => u.trim()).filter(Boolean);
  } else if (Array.isArray(pictureField)) {
    urls = pictureField.filter((u): u is string => typeof u === 'string' && u.trim().length > 0);
  }
  return urls.map(url => {
    const localPath = urlToLocalMap.get(url);
    return localPath || url;
  });
}

let urlToLocalMap = new Map<string, string>();

async function generateProductPage(
  idx: number,
  productId: string,
  productName: string,
  comparisons: ComparisonRow[],
  images: string[],
  allProducts: { id: string; name: string }[]
): Promise<string> {
  const matchCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'MATCH').length;
  const noMatchCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'NO_MATCH').length;
  const uncertainCount = comparisons.filter(c => c.comparison_result?.toUpperCase() === 'UNCERTAIN').length;

  let html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(productName)} - 专利侵权比对报告</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; color: #1a1a1a; line-height: 1.6; padding: 2rem; }
    .container { max-width: 1400px; margin: 0 auto; }
    .header { background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1.5rem; margin-bottom: 1.5rem; }
    .header h1 { font-size: 1.25rem; margin-bottom: 0.5rem; }
    .header .meta { font-size: 0.85rem; color: #666; }
    .product-images { display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }
    .product-images img { width: 80px; height: 80px; object-fit: cover; border-radius: 6px; border: 1px solid #e5e5e5; cursor: pointer; }
    .stats { display: flex; gap: 1rem; margin-top: 0.75rem; }
    .stat { padding: 0.25rem 0.75rem; border-radius: 999px; font-size: 0.8rem; font-weight: 500; }
    .stat.match { background: #fef2f2; color: #dc2626; }
    .stat.no-match { background: #f0fdf4; color: #16a34a; }
    .stat.uncertain { background: #fffbeb; color: #d97706; }
    .nav-bar { background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1rem 1.5rem; margin-bottom: 1.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    .nav-bar .label { font-size: 0.85rem; color: #666; margin-right: 0.5rem; }
    .nav-bar a { padding: 0.35rem 0.75rem; border-radius: 6px; font-size: 0.8rem; text-decoration: none; border: 1px solid #e5e5e5; color: #333; }
    .nav-bar a:hover { background: #f0f0f0; }
    .nav-bar a.active { background: #3b82f6; color: white; border-color: #3b82f6; }
    table { width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    thead th { background: #f9fafb; padding: 0.75rem 1rem; text-align: left; font-size: 0.8rem; font-weight: 600; color: #374151; border-bottom: 2px solid #e5e7eb; }
    thead th:first-child { width: 70px; text-align: center; }
    thead th:nth-child(3) { width: 100px; text-align: center; }
    thead th:nth-child(4) { width: 220px; }
    tbody td { padding: 1rem; border-bottom: 1px solid #f0f0f0; font-size: 0.9rem; vertical-align: top; }
    tbody td:first-child { text-align: center; color: #666; font-size: 0.85rem; font-weight: 500; }
    tbody td:nth-child(3) { text-align: center; }
    tbody tr:hover { background: #fafafa; }
    .badge { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 500; }
    .badge.match { background: #fef2f2; color: #dc2626; }
    .badge.no-match { background: #f0fdf4; color: #16a34a; }
    .badge.uncertain { background: #fffbeb; color: #d97706; }
    .evidence { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: #f0f9ff; border-radius: 6px; font-size: 0.8rem; color: #0369a1; line-height: 1.4; }
    .reasoning { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: #f9fafb; border-radius: 6px; font-size: 0.85rem; color: #4b5563; line-height: 1.5; }
    .feature-text { font-weight: 500; }
    .feature-image { margin-top: 0.5rem; display: flex; gap: 0.25rem; flex-wrap: wrap; }
    .feature-image img { max-width: 150px; max-height: 100px; object-fit: contain; border-radius: 4px; border: 1px solid #e5e5e5; cursor: pointer; }
    .feature-image .no-image { font-size: 0.75rem; color: #999; font-style: italic; }
    .image-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; justify-content: center; align-items: center; cursor: pointer; }
    .image-modal.active { display: flex; }
    .image-modal img { max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 8px; }
    .back-link { display: inline-block; margin-bottom: 1rem; color: #3b82f6; text-decoration: none; font-size: 0.9rem; }
    .back-link:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <a href="index.html" class="back-link">← 返回商品列表</a>
    <div class="header">
      <h1>${idx}. ${escapeHtml(productName)}</h1>
      <p class="meta">产品ID: ${escapeHtml(productId)}</p>
      ${images.length > 0 ? `
      <div class="product-images">
        ${images.slice(0, 8).map(img => `<img src="${escapeHtml(img)}" alt="商品图片" onclick="openModal('${escapeHtml(img)}')" onerror="this.style.display='none'">`).join('')}
        ${images.length > 8 ? `<span style="line-height:80px;color:#999;font-size:0.8rem;">+${images.length - 8} 张</span>` : ''}
      </div>` : ''}
      <div class="stats">
        <span class="stat match">✅ 相同/等同: ${matchCount}</span>
        <span class="stat no-match">❌ 不相同: ${noMatchCount}</span>
        <span class="stat uncertain">⚠️ 不确定: ${uncertainCount}</span>
      </div>
    </div>
    <div class="nav-bar">
      <span class="label">切换商品:</span>
      ${allProducts.map(p => `<a href="product-${p.id}.html" class="${p.id === productId ? 'active' : ''}">${p.name.slice(0, 20)}${p.name.length > 20 ? '...' : ''}</a>`).join('')}
    </div>
    <table>
      <thead>
        <tr>
          <th>特征编号</th>
          <th>专利权利要求特征</th>
          <th>比对结论</th>
          <th>商品特征证据</th>
          <th>分析依据</th>
        </tr>
      </thead>
      <tbody>
`;

  for (const comp of comparisons) {
    const config = resultToConfig(comp.comparison_result || '');
    const badgeClass = comp.comparison_result?.toUpperCase() === 'MATCH' ? 'match' 
      : comp.comparison_result?.toUpperCase() === 'NO_MATCH' ? 'no-match' : 'uncertain';
    
    const evidenceText = comp.evidence || '';
    const featureImages = comp.evidence_images && comp.evidence_images.length > 0 
      ? comp.evidence_images 
      : [];
    
    const featureImageHtml = featureImages.length > 0
      ? `<div class="feature-image">${featureImages.slice(0, 3).map(img => `<img src="${escapeHtml(img)}" alt="证据图片" onclick="openModal('${escapeHtml(img)}')" onerror="this.style.display='none'">`).join('')}</div>`
      : `<div class="feature-image"><span class="no-image">图片中无法体现</span></div>`;

    html += `
        <tr>
          <td>${escapeHtml(comp.feature_id || '')}</td>
          <td class="feature-text">${escapeHtml(comp.feature_text || '')}</td>
          <td><span class="badge ${badgeClass}">${config.icon} ${config.label}</span></td>
          <td>
            ${featureImageHtml}
            ${evidenceText ? `<div class="evidence">${escapeHtml(evidenceText)}</div>` : ''}
          </td>
          <td><div class="reasoning">${escapeHtml(comp.reason || '')}</div></td>
        </tr>
`;
  }

  html += `
      </tbody>
    </table>
  </div>
  <div class="image-modal" id="imageModal" onclick="this.classList.remove('active')">
    <img id="modalImage" src="" alt="">
  </div>
  <script>
    function openModal(src) {
      document.getElementById('modalImage').src = src;
      document.getElementById('imageModal').classList.add('active');
    }
  </script>
</body>
</html>`;

  return html;
}

async function generateIndexPage(products: { id: string; name: string; matchCount: number; noMatchCount: number; uncertainCount: number; total: number }[]): Promise<string> {
  let html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>专利侵权比对报告 - 商品列表</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background: #f5f5f5; color: #1a1a1a; line-height: 1.6; padding: 2rem; }
    .container { max-width: 1200px; margin: 0 auto; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    .subtitle { color: #666; margin-bottom: 2rem; font-size: 0.9rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 1rem; }
    .card { background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1.25rem; text-decoration: none; color: inherit; transition: box-shadow 0.2s; }
    .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
    .card h2 { font-size: 1rem; margin-bottom: 0.5rem; }
    .card .meta { font-size: 0.8rem; color: #666; margin-bottom: 0.75rem; }
    .card .stats { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .card .stat { padding: 0.2rem 0.5rem; border-radius: 999px; font-size: 0.75rem; font-weight: 500; }
    .card .stat.match { background: #fef2f2; color: #dc2626; }
    .card .stat.no-match { background: #f0fdf4; color: #16a34a; }
    .card .stat.uncertain { background: #fffbeb; color: #d97706; }
  </style>
</head>
<body>
  <div class="container">
    <h1>专利侵权比对报告</h1>
    <p class="subtitle">基于专利 CN218484462U 扫地机器人实用新型 | 共 ${products.length} 个商品</p>
    <div class="grid">
`;

  for (let i = 0; i < products.length; i++) {
    const p = products[i];
    html += `
      <a href="product-${p.id}.html" class="card">
        <h2>${i + 1}. ${escapeHtml(p.name)}</h2>
        <p class="meta">产品ID: ${escapeHtml(p.id)} | 共 ${p.total} 个特征</p>
        <div class="stats">
          <span class="stat match">✅ ${p.matchCount}</span>
          <span class="stat no-match">❌ ${p.noMatchCount}</span>
          <span class="stat uncertain">⚠️ ${p.uncertainCount}</span>
        </div>
      </a>
`;
  }

  html += `
    </div>
  </div>
</body>
</html>`;

  return html;
}

async function main() {
  const patentRecordId = 26;

  console.log('正在生成 Claim Chart HTML 报告...');

  try {
    // 获取最新的 claim_compare_run_id
    const runIdResult = await pgQuery('SELECT MAX(id) as max_id FROM claim_compare_runs');
    const latestRunId = Number(runIdResult.rows[0]?.max_id || 3);
    console.log(`使用 claim_compare_run_id: ${latestRunId}`);

    // 查询比对结果
    const rows = await pgQuery<ComparisonRow>(`
      SELECT id, product_id, product_name, feature_id, feature_text, evidence, comparison_result, reason, evidence_images
      FROM claim_compare_results 
      WHERE claim_compare_run_id = $1 
      ORDER BY product_id, id ASC
    `, [latestRunId]);

    if (rows.rows.length === 0) {
      console.log('无比对结果');
      return;
    }

    // 加载 URL 映射
    const mappingPath = path.join(DATA_DIR, 'image-url-mapping.json');
    try {
      const mappingContent = await readFile(mappingPath, 'utf-8');
      const mapping = JSON.parse(mappingContent);
      urlToLocalMap = new Map(Object.entries(mapping));
      console.log(`已加载 ${urlToLocalMap.size} 个 URL 映射`);
    } catch {
      console.log('未找到 URL 映射文件');
    }

    // 查询商品图片
    const imageRows = await pgQuery(`
      SELECT product_id, picture
      FROM search_products 
      WHERE patent_record_id = $1
    `, [patentRecordId]);

    const productImages = new Map<string, string[]>();
    for (const row of imageRows.rows) {
      productImages.set(row.product_id, parsePictures(row.picture));
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

    // 创建输出目录
    await mkdir(OUTPUT_DIR, { recursive: true });

    // 生成商品列表
    const productList = Array.from(productMap.entries()).map(([id, comps]) => ({
      id,
      name: comps[0]?.product_name || id,
      matchCount: comps.filter(c => c.comparison_result?.toUpperCase() === 'MATCH').length,
      noMatchCount: comps.filter(c => c.comparison_result?.toUpperCase() === 'NO_MATCH').length,
      uncertainCount: comps.filter(c => c.comparison_result?.toUpperCase() === 'UNCERTAIN').length,
      total: comps.length,
    }));

    // 生成索引页
    const indexHtml = await generateIndexPage(productList);
    await writeFile(path.join(OUTPUT_DIR, 'index.html'), indexHtml);
    console.log(`索引页已生成: ${path.join(OUTPUT_DIR, 'index.html')}`);

    // 生成每个商品页面
    let idx = 0;
    for (const [productId, comparisons] of productMap.entries()) {
      idx++;
      const productName = comparisons[0]?.product_name || productId;
      const images = productImages.get(productId) || [];
      
      const pageHtml = await generateProductPage(
        idx,
        productId,
        productName,
        comparisons,
        images,
        productList
      );
      
      await writeFile(path.join(OUTPUT_DIR, `product-${productId}.html`), pageHtml);
      console.log(`商品页面已生成: product-${productId}.html`);
    }

    console.log(`\n✅ 报告已生成到: ${OUTPUT_DIR}`);
    console.log(`   共 ${productMap.size} 个商品页面 + 1 个索引页`);
    console.log(`   打开索引页查看: open ${path.join(OUTPUT_DIR, 'index.html')}`);

  } catch (error) {
    console.error('生成失败:', error);
  } finally {
    process.exit(0);
  }
}

main();
