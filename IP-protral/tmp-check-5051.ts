import path from 'path';
import { config } from 'dotenv';
import { Client } from 'pg';

async function main() {
  config({ path: path.resolve(process.cwd(), '.env.local') });
  const client = new Client({ connectionString: process.env.PGDATABASE_URL || process.env.DATABASE_URL });
  await client.connect();
  const res = await client.query(`
    select id, analysis_session_id, successful_keywords_count, failed_keywords_count, total_products_count, is_complete, error_message, created_at
    from search_runs
    where id in (50, 51)
    order by id asc
  `);
  console.log(JSON.stringify(res.rows, null, 2));
  await client.end();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
