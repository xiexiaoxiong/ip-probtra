import { Pool, type PoolClient, type QueryResult, type QueryResultRow } from 'pg';

declare global {
  var __patentPgPool: Pool | undefined;
}

function getConnectionString(): string {
  const connectionString = process.env.PGDATABASE_URL || process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error('缺少数据库连接配置 `PGDATABASE_URL`');
  }
  return connectionString;
}

export function getPostgresPool(): Pool {
  if (!global.__patentPgPool) {
    global.__patentPgPool = new Pool({
      connectionString: getConnectionString(),
      max: 5,
    });
  }
  return global.__patentPgPool;
}

export async function pgQuery<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params: unknown[] = [],
): Promise<QueryResult<T>> {
  return getPostgresPool().query<T>(text, params);
}

export async function withPgClient<T>(callback: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await getPostgresPool().connect();
  try {
    return await callback(client);
  } finally {
    client.release();
  }
}
