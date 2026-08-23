import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import { pgQuery } from '@/lib/postgres';
import type { AnalysisSession, PatentInfo, ProductInfo } from '@/lib/types';

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

  if (!session || session.analysisKind !== 'infringement') {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权访问该分析会话' }, { status: 403 });
  }

  const enrichedPatentSession = await enrichSessionPatentFromModule1(session);
  const enrichedSession = await enrichSessionProductsWithBrand(enrichedPatentSession);

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

async function enrichSessionPatentFromModule1(session: AnalysisSession): Promise<AnalysisSession> {
  const patentRecordId = session.results?.dbRecordId;
  if (!patentRecordId) {
    return session;
  }

  const currentPatent = session.results?.patent || {};
  const needsPatent =
    !currentPatent.title ||
    !currentPatent.patentNumber ||
    !currentPatent.abstract ||
    !currentPatent.drawings?.length ||
    !currentPatent.independentClaims?.length;

  if (!needsPatent) {
    return session;
  }

  const [recordResult, claimsResult, figuresResult] = await Promise.all([
    pgQuery<Record<string, unknown>>(
      `select patent_number, title, abstract_text, specification from patent_parse_records where id = $1 limit 1`,
      [patentRecordId],
    ),
    pgQuery<Record<string, unknown>>(
      `select claim_type, claim_text from patent_claims where record_id = $1 order by id asc`,
      [patentRecordId],
    ),
    pgQuery<Record<string, unknown>>(
      `select figure_url from patent_figures where record_id = $1 order by id asc`,
      [patentRecordId],
    ),
  ]);

  const record = recordResult.rows[0] || {};
  const specification = normalizeSpecification(record['specification']);
  const independentClaims = claimsResult.rows
    .filter((row) => row['claim_type'] === 'INDEPENDENT' && row['claim_text'])
    .map((row) => String(row['claim_text']));
  const dependentClaims = claimsResult.rows
    .filter((row) => row['claim_type'] === 'DEPENDENT' && row['claim_text'])
    .map((row) => String(row['claim_text']));
  const drawings = figuresResult.rows
    .map((row) => row['figure_url'])
    .filter((url): url is string => typeof url === 'string' && url.trim().length > 0);

  const patent: PatentInfo = {
    ...currentPatent,
    title: currentPatent.title || asString(record['title']) || inferPatentTitleFromClaims(independentClaims),
    patentNumber: currentPatent.patentNumber || asString(record['patent_number']),
    abstract: currentPatent.abstract || asString(record['abstract_text']) || extractPatentAbstract(record['specification']),
    independentClaims: currentPatent.independentClaims?.length ? currentPatent.independentClaims : independentClaims,
    dependentClaims: currentPatent.dependentClaims?.length ? currentPatent.dependentClaims : dependentClaims,
    specification: currentPatent.specification || specification,
    drawings: currentPatent.drawings?.length ? currentPatent.drawings : drawings,
  };

  return {
    ...session,
    patentTitle: session.patentTitle || patent.title || null,
    patentNumber: session.patentNumber || patent.patentNumber || null,
    results: session.results
      ? {
          ...session.results,
          patent,
        }
      : session.results,
  };
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function normalizeSpecification(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const text = Object.entries(value as Record<string, unknown>)
    .map(([section, content]) => `${section}\n${String(content || '')}`)
    .join('\n\n')
    .trim();
  return text || undefined;
}

function extractPatentAbstract(specification: unknown): string | undefined {
  if (!specification || typeof specification !== 'object') return undefined;
  for (const [section, content] of Object.entries(specification as Record<string, unknown>)) {
    if (!section.includes('摘要')) continue;
    const text = String(content || '').trim();
    if (text) return text;
  }
  return undefined;
}

function inferPatentTitleFromClaims(independentClaims: string[]): string | undefined {
  const firstClaim = independentClaims.find((claim) => claim.trim());
  if (!firstClaim) return undefined;
  const normalized = firstClaim.replace(/\s+/g, '');
  const match = normalized.match(/(?:^\d+[.、:：])?(一种[^，。,；;:：]{1,40}?)(?:，?其特征在于|包括|至少包括|，)/);
  return match?.[1];
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
