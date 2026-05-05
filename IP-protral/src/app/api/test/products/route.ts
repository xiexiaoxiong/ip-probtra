import { NextRequest, NextResponse } from 'next/server';
import { withPgClient } from '@/lib/postgres';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const searchRunId = searchParams.get('search_run_id');
  const patentRecordId = searchParams.get('patent_record_id');

  try {
    const products = await withPgClient(async (client) => {
      let query: string;
      let params: unknown[];

      if (searchRunId) {
        query = `
          SELECT id, product_id, product_name, product_url, product_source,
                 price, brand, manufacturer, matched_keywords, description, picture
          FROM search_products
          WHERE search_run_id = $1
          ORDER BY id ASC
        `;
        params = [parseInt(searchRunId, 10)];
      } else if (patentRecordId) {
        query = `
          SELECT id, product_id, product_name, product_url, product_source,
                 price, brand, manufacturer, matched_keywords, description, picture
          FROM search_products
          WHERE patent_record_id = $1
          ORDER BY id ASC
        `;
        params = [parseInt(patentRecordId, 10)];
      } else {
        query = `
          SELECT id, product_id, product_name, product_url, product_source,
                 price, brand, manufacturer, matched_keywords, description, picture
          FROM search_products
          ORDER BY id DESC
          LIMIT 50
        `;
        params = [];
      }

      const result = await client.query(query, params);
      return result.rows;
    });

    return NextResponse.json(products);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 500 },
    );
  }
}