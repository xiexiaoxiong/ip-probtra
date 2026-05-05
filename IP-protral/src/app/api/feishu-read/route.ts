import { NextResponse } from 'next/server';
import {
  readAnalysisResults,
  mapPatentInfo,
  mapProducts,
  mapComparisons,
} from '@/lib/feishu-client';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

interface FeishuReadRequest {
  feishuUrl: string;
  appId: string;
  appSecret: string;
}

export async function POST(request: Request) {
  try {
    const body: FeishuReadRequest = await request.json();

    if (!body.feishuUrl) {
      return NextResponse.json({ error: '缺少 feishuUrl 参数' }, { status: 400 });
    }

    if (!body.appId || !body.appSecret) {
      return NextResponse.json({ error: '缺少飞书 API 凭证（appId 和 appSecret）' }, { status: 400 });
    }

    // 临时设置飞书凭证（仅本次请求有效）
    process.env.FEISHU_APP_ID = body.appId;
    process.env.FEISHU_APP_SECRET = body.appSecret;

    // 清除 token 缓存，确保使用新的凭证
    const { clearTokenCache } = await import('@/lib/feishu-client');
    clearTokenCache();

    // 读取飞书表格数据
    const feishuData = await readAnalysisResults(body.feishuUrl);

    let products: ReturnType<typeof mapProducts> = [];
    let comparisons: ReturnType<typeof mapComparisons> = [];

    for (const [tableName, records] of Object.entries(feishuData.tables)) {
      if (records.length === 0) continue;

      const nameLower = tableName.toLowerCase();

      if (nameLower.includes('商品') || nameLower.includes('product') || nameLower.includes('产品') || nameLower.includes('检索')) {
        products = mapProducts(records);
      } else if (nameLower.includes('比对') || nameLower.includes('comparison') || nameLower.includes('侵权') || nameLower.includes('特征')) {
        comparisons = mapComparisons(records);
      } else {
        // 自动判断
        const fields = Object.keys(records[0]);
        const hasProductFields = fields.some(f => f.includes('商品') || f.includes('产品') || f.includes('product'));
        const hasClaimFields = fields.some(f => f.includes('权利要求') || f.includes('特征') || f.includes('claim') || f.includes('比对'));

        if (hasProductFields && !hasClaimFields) {
          products = mapProducts(records);
        } else if (hasClaimFields) {
          comparisons = mapComparisons(records);
        }
      }
    }

    // 如果没有找到比对数据但有多条商品记录，可能比对和商品在同一表格
    if (comparisons.length === 0 && products.length > 0) {
      const sourceTable = Object.values(feishuData.tables).find(records =>
        records.length > 0 && Object.keys(records[0]).some(f =>
          f.includes('权利要求') || f.includes('技术特征') || f.includes('claim') ||
          f.includes('匹配') || f.includes('比对') || f.includes('侵权')
        )
      );
      if (sourceTable) {
        comparisons = mapComparisons(sourceTable);
      }
    }

    return NextResponse.json({
      products,
      comparisons,
      patent: Object.entries(feishuData.tables).find(([name]) =>
        name.toLowerCase().includes('专利') || name.toLowerCase().includes('patent')
      )?.[1] ? mapPatentInfo(
        Object.entries(feishuData.tables).find(([name]) =>
          name.toLowerCase().includes('专利') || name.toLowerCase().includes('patent')
        )![1]
      ) : undefined,
    });
  } catch (e) {
    console.error('[Feishu Read API] Error:', e);
    return NextResponse.json(
      { error: e instanceof Error ? e.message : '读取飞书数据失败' },
      { status: 500 },
    );
  }
}
