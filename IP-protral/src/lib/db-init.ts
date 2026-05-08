import { pgQuery } from './postgres';
import { hashPassword } from './password';

let initPromise: Promise<void> | null = null;

function getBootstrapAdminConfig(): { email: string; password: string; name: string } | null {
  const email = process.env.AUTH_BOOTSTRAP_ADMIN_EMAIL?.trim();
  const password = process.env.AUTH_BOOTSTRAP_ADMIN_PASSWORD?.trim();
  const name = process.env.AUTH_BOOTSTRAP_ADMIN_NAME?.trim() || '系统管理员';

  if (!email || !password) {
    return null;
  }

  return { email, password, name };
}

async function ensureCoreTables(): Promise<void> {
  await pgQuery(`
    create table if not exists users (
      id serial primary key,
      name text not null,
      email text not null unique,
      password_hash text not null,
      role text not null default 'user',
      status text not null default 'pending',
      approved_by integer references users(id) on delete set null,
      approved_at timestamptz,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists auth_sessions (
      id text primary key,
      user_id integer not null references users(id) on delete cascade,
      expires_at timestamptz not null,
      created_at timestamptz not null default now(),
      last_seen_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists analysis_sessions (
      id text primary key,
      user_id integer references users(id) on delete cascade,
      status text not null default 'pending',
      input_type text not null,
      input_value text,
      file_name text,
      file_url text,
      text_content text,
      patent_title text,
      patent_number text,
      results jsonb not null default '{}'::jsonb,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists analysis_steps (
      id serial primary key,
      session_id text not null references analysis_sessions(id) on delete cascade,
      step_id integer not null,
      step_name text not null,
      status text not null default 'pending',
      error text,
      started_at timestamptz,
      completed_at timestamptz,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now(),
      unique (session_id, step_id)
    )
  `);

  await pgQuery(`
    create table if not exists error_reports (
      id serial primary key,
      analysis_session_id text references analysis_sessions(id) on delete set null,
      user_id integer references users(id) on delete set null,
      step_id integer,
      step_name text,
      error_message text not null,
      error_stack text,
      patent_text text,
      input_type text,
      input_value text,
      file_url text,
      meta jsonb not null default '{}'::jsonb,
      created_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists patent_parse_records (
      id serial primary key,
      task_id text not null unique,
      patent_number text,
      patent_holder text,
      title text,
      application_date text,
      priority_date text,
      specification jsonb,
      parse_errors jsonb,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists keyword_runs (
      id serial primary key,
      patent_record_id integer not null,
      analysis_session_id text,
      workflow_variant text,
      keywords_count integer not null default 0,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists keyword_records (
      id serial primary key,
      keyword_run_id integer not null,
      patent_record_id integer not null,
      analysis_session_id text,
      keyword_id text,
      claim_id text,
      keyword_text text not null,
      keyword_type text,
      source_location text,
      generation_method text,
      confidence_score double precision,
      raw_payload jsonb,
      created_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists search_runs (
      id serial primary key,
      patent_record_id integer not null,
      analysis_session_id text,
      product_dataset_id text,
      retrieval_start_time text,
      successful_keywords_count integer not null default 0,
      failed_keywords_count integer not null default 0,
      total_products_count integer not null default 0,
      platforms_queried jsonb,
      is_complete boolean not null default true,
      error_message text,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists search_products (
      id serial primary key,
      search_run_id integer not null,
      patent_record_id integer not null,
      analysis_session_id text,
      product_id text,
      product_name text,
      product_url text,
      product_source text,
      price text,
      brand text,
      manufacturer text,
      matched_keywords text,
      description text,
      picture jsonb,
      raw_payload jsonb,
      created_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists claim_compare_runs (
      id serial primary key,
      patent_record_id integer not null,
      analysis_session_id text,
      result_summary text,
      product_count integer not null default 0,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now()
    )
  `);

  await pgQuery(`
    create table if not exists claim_compare_results (
      id serial primary key,
      claim_compare_run_id integer not null,
      patent_record_id integer not null,
      analysis_session_id text,
      product_id text,
      product_name text,
      claim_id text,
      feature_id text,
      feature_text text,
      evidence text,
      comparison_result text,
      reason text,
      reasoning_type text,
      evidence_images jsonb,
      raw_payload jsonb,
      created_at timestamptz not null default now()
    )
  `);

  await pgQuery(`alter table analysis_sessions add column if not exists user_id integer references users(id) on delete cascade`);
  await pgQuery(`alter table analysis_sessions add column if not exists patent_title text`);
  await pgQuery(`alter table analysis_sessions add column if not exists patent_number text`);
  await pgQuery(`alter table analysis_steps add column if not exists started_at timestamptz`);
  await pgQuery(`alter table analysis_steps add column if not exists completed_at timestamptz`);

  await pgQuery(`create index if not exists idx_users_status on users(status)`);
  await pgQuery(`create index if not exists idx_auth_sessions_user_id on auth_sessions(user_id)`);
  await pgQuery(`create index if not exists idx_auth_sessions_expires_at on auth_sessions(expires_at)`);
  await pgQuery(`create index if not exists idx_analysis_sessions_user_id on analysis_sessions(user_id)`);
  await pgQuery(`create index if not exists idx_analysis_sessions_status on analysis_sessions(status)`);
  await pgQuery(`create index if not exists idx_analysis_sessions_created_at on analysis_sessions(created_at desc)`);
  await pgQuery(`create index if not exists idx_analysis_steps_session_id on analysis_steps(session_id)`);
  await pgQuery(`create index if not exists idx_error_reports_created_at on error_reports(created_at desc)`);
  await pgQuery(`create index if not exists idx_error_reports_session_id on error_reports(analysis_session_id)`);
}

async function ensureBootstrapAdmin(): Promise<void> {
  const config = getBootstrapAdminConfig();
  if (!config) {
    return;
  }

  const existing = await pgQuery<{ id: number }>('select id from users where email = $1 limit 1', [config.email]);
  if (existing.rowCount && existing.rows[0]) {
    await pgQuery(
      `
        update users
        set role = 'admin',
            status = 'approved',
            approved_at = coalesce(approved_at, now()),
            updated_at = now()
        where id = $1
      `,
      [existing.rows[0].id],
    );
    return;
  }

  const passwordHash = await hashPassword(config.password);
  await pgQuery(
    `
      insert into users (name, email, password_hash, role, status, approved_at)
      values ($1, $2, $3, 'admin', 'approved', now())
    `,
    [config.name, config.email, passwordHash],
  );
}

export async function ensureDatabaseReady(): Promise<void> {
  if (!initPromise) {
    initPromise = (async () => {
      await ensureCoreTables();
      await ensureBootstrapAdmin();
    })().catch((error) => {
      initPromise = null;
      throw error;
    });
  }

  await initPromise;
}
