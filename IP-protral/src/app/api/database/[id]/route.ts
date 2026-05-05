import { NextRequest, NextResponse } from 'next/server';
import { getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import { withPgClient } from '@/lib/postgres';

export const dynamic = 'force-dynamic';

type JsonRecord = Record<string, unknown>;

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function tableExists(
  client: { query: (sql: string, params?: unknown[]) => Promise<{ rows: Array<Record<string, unknown>> }> },
  tableName: string,
): Promise<boolean> {
  const result = await client.query('select to_regclass($1) as exists', [tableName]);
  return Boolean(result.rows[0]?.exists);
}

async function selectAllIfExists(
  client: { query: (sql: string, params?: unknown[]) => Promise<{ rows: JsonRecord[] }> },
  tableName: string,
  sql: string,
  params: unknown[] = [],
): Promise<JsonRecord[]> {
  if (!(await tableExists(client, tableName))) {
    return [];
  }
  return (await client.query(sql, params)).rows;
}

async function selectOneIfExists(
  client: { query: (sql: string, params?: unknown[]) => Promise<{ rows: JsonRecord[] }> },
  tableName: string,
  sql: string,
  params: unknown[] = [],
): Promise<JsonRecord | null> {
  if (!(await tableExists(client, tableName))) {
    return null;
  }
  return (await client.query(sql, params)).rows[0] || null;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return NextResponse.json({ error: '请先登录' }, { status: 401 });
  }

  const { id } = await params;
  const session = await getSessionAsync(id);

  if (!session) {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权访问该数据库快照' }, { status: 403 });
  }

  try {
    const snapshot = await withPgClient(async (client) => {
      const resultIds = session.results || {};
      let patentRecordId =
        typeof resultIds.dbRecordId === 'number' && resultIds.dbRecordId > 0
          ? resultIds.dbRecordId
          : undefined;

      const keywordRuns = await selectAllIfExists(
        client,
        'keyword_runs',
        'select * from keyword_runs where analysis_session_id = $1 order by id desc',
        [id],
      );

      const searchRuns = await selectAllIfExists(
        client,
        'search_runs',
        'select * from search_runs where analysis_session_id = $1 order by id desc',
        [id],
      );

      const claimCompareRuns = await selectAllIfExists(
        client,
        'claim_compare_runs',
        'select * from claim_compare_runs where analysis_session_id = $1 order by id desc',
        [id],
      );

      if (!patentRecordId) {
        const inferred = [keywordRuns[0], searchRuns[0], claimCompareRuns[0]]
          .map((row) => Number(row?.patent_record_id || 0))
          .find((value) => value > 0);
        patentRecordId = inferred;
      }

      const patentRecord = patentRecordId
        ? await selectOneIfExists(
            client,
            'patent_parse_records',
            'select * from patent_parse_records where id = $1 limit 1',
            [patentRecordId],
          )
        : null;

      const patentClaims = patentRecordId
        ? await selectAllIfExists(
            client,
            'patent_claims',
            'select * from patent_claims where record_id = $1 order by id asc',
            [patentRecordId],
          )
        : [];

      const patentFigures = patentRecordId
        ? await selectAllIfExists(
            client,
            'patent_figures',
            'select * from patent_figures where record_id = $1 order by id asc',
            [patentRecordId],
          )
        : [];

      const keywordRunIds = keywordRuns.map((row) => Number(row.id)).filter((value) => value > 0);
      const keywordRecords = keywordRunIds.length > 0
        ? await selectAllIfExists(
            client,
            'keyword_records',
            'select * from keyword_records where keyword_run_id = any($1::int[]) order by id asc',
            [keywordRunIds],
          )
        : [];

      const searchRunIds = searchRuns.map((row) => Number(row.id)).filter((value) => value > 0);
      const searchProducts = searchRunIds.length > 0
        ? await selectAllIfExists(
            client,
            'search_products',
            'select * from search_products where search_run_id = any($1::int[]) order by id asc',
            [searchRunIds],
          )
        : [];

      const claimCompareRunIds = claimCompareRuns.map((row) => Number(row.id)).filter((value) => value > 0);
      const claimCompareResults = claimCompareRunIds.length > 0
        ? await selectAllIfExists(
            client,
            'claim_compare_results',
            'select * from claim_compare_results where claim_compare_run_id = any($1::int[]) order by id asc',
            [claimCompareRunIds],
          )
        : [];

      return {
        sessionId: id,
        ids: {
          patentRecordId: patentRecordId || null,
          keywordRunId:
            typeof resultIds.keywordRunId === 'number' && resultIds.keywordRunId > 0
              ? resultIds.keywordRunId
              : keywordRunIds[0] || null,
          searchRunId:
            typeof resultIds.searchRunId === 'number' && resultIds.searchRunId > 0
              ? resultIds.searchRunId
              : searchRunIds[0] || null,
          claimCompareRunId:
            typeof resultIds.claimCompareRunId === 'number' && resultIds.claimCompareRunId > 0
              ? resultIds.claimCompareRunId
              : claimCompareRunIds[0] || null,
        },
        counts: {
          patentClaims: patentClaims.length,
          patentFigures: patentFigures.length,
          keywordRuns: keywordRuns.length,
          keywordRecords: keywordRecords.length,
          searchRuns: searchRuns.length,
          searchProducts: searchProducts.length,
          claimCompareRuns: claimCompareRuns.length,
          claimCompareResults: claimCompareResults.length,
        },
        tables: {
          patentParseRecord: patentRecord,
          patentClaims,
          patentFigures,
          keywordRuns,
          keywordRecords,
          searchRuns,
          searchProducts,
          claimCompareRuns,
          claimCompareResults,
        },
      };
    });

    return NextResponse.json({
      session,
      snapshot,
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: `读取数据库快照失败: ${toErrorMessage(error)}`,
      },
      { status: 500 },
    );
  }
}
