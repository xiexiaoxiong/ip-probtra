import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import { pgQuery } from '@/lib/postgres';
import type { AnalysisSession, ProductInfo } from '@/lib/types';

export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return createUnauthorizedResponse(request);
  }

  const { id } = await params;
  const session = await getSessionAsync(id);

  if (!session) {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权访问该分析会话' }, { status: 403 });
  }

  const enrichedSession = await enrichSessionProductsWithBrand(session);

  return NextResponse.json(
    { session: enrichedSession },
    {
      headers: {
        'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
        Pragma: 'no-cache',
        Expires: '0',
      },
    },
  );
}

async function enrichSessionProductsWithBrand(session: AnalysisSession): Promise<AnalysisSession> {
  const products = session.results?.products;
  const patentRecordId = session.results?.dbRecordId;
  if (!Array.isArray(products) || products.length === 0 || !patentRecordId) {
    return session;
  }

  const needsBrand = products.some((product) => !product.brand);
  if (!needsBrand) {
    return session;
  }

  const result = await pgQuery<Record<string, unknown>>(
    `SELECT id, product_id, product_name, brand, manufacturer
     FROM search_products
     WHERE patent_record_id = $1
       AND analysis_session_id = $2`,
    [patentRecordId, session.id],
  );

  if (result.rows.length === 0) {
    return session;
  }

  const brandMap = new Map<string, { brand?: string; manufacturer?: string }>();
  for (const row of result.rows) {
    const keys = [
      row['id'] != null ? String(row['id']) : '',
      row['product_id'] ? String(row['product_id']) : '',
      row['product_name'] ? String(row['product_name']) : '',
    ].filter(Boolean);
    const value = {
      brand: row['brand'] ? String(row['brand']) : undefined,
      manufacturer: row['manufacturer'] ? String(row['manufacturer']) : undefined,
    };
    for (const key of keys) {
      if (!brandMap.has(key)) {
        brandMap.set(key, value);
      }
    }
  }

  const enrichedProducts: ProductInfo[] = products.map((product) => {
    const matched = brandMap.get(product.id) || brandMap.get(product.name);
    return {
      ...product,
      brand: product.brand || matched?.brand,
      manufacturer: product.manufacturer || matched?.manufacturer,
      company: product.company || matched?.manufacturer,
    };
  });

  return {
    ...session,
    results: session.results
      ? {
          ...session.results,
          products: enrichedProducts,
        }
      : session.results,
  };
}
