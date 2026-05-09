import { readFile } from 'fs/promises';
import path from 'path';
import type { QueryResultRow } from 'pg';
import type {
  AuthUser,
  AnalysisInput,
  AnalysisResults,
  AnalysisSession,
  AnalysisStatus,
  AnalysisStep,
  StepStatus,
} from './types';
import { ensureDatabaseReady } from './db-init';
import { pgQuery } from './postgres';
import { WORKFLOW_MODULES } from './types';
import { getSessionsDir } from './runtime-paths';

const memoryStore = new Map<string, AnalysisSession>();

interface SessionRow extends QueryResultRow {
  id: string;
  user_id: number;
  user_name: string;
  status: AnalysisStatus;
  input_type: AnalysisInput['type'];
  input_value: string | null;
  file_name: string | null;
  file_url: string | null;
  text_content: string | null;
  patent_title: string | null;
  patent_number: string | null;
  results: AnalysisResults | null;
  created_at: string;
  updated_at: string;
}

interface StepRow extends QueryResultRow {
  step_id: number;
  step_name: string;
  status: StepStatus;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

function createDefaultSteps(): AnalysisStep[] {
  return WORKFLOW_MODULES.map((module) => ({
    id: module.id,
    name: module.name,
    description: module.description,
    status: 'pending' as StepStatus,
  }));
}

function toTimestamp(value?: string | null): number {
  if (!value) {
    return Date.now();
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function getStepDescription(stepId: number): string {
  return WORKFLOW_MODULES.find((module) => module.id === stepId)?.description || '';
}

function mapStepRow(row: StepRow): AnalysisStep {
  return {
    id: row.step_id,
    name: row.step_name,
    description: getStepDescription(row.step_id),
    status: row.status,
    error: row.error || undefined,
    startedAt: row.started_at ? toTimestamp(row.started_at) : undefined,
    completedAt: row.completed_at ? toTimestamp(row.completed_at) : undefined,
  };
}

function mapDbSession(row: SessionRow, steps: AnalysisStep[]): AnalysisSession {
  return {
    id: row.id,
    userId: row.user_id,
    userName: row.user_name,
    status: row.status,
    input: {
      type: row.input_type,
      value: row.input_value || '',
      fileName: row.file_name || undefined,
      fileUrl: row.file_url || undefined,
      text: row.text_content || undefined,
    },
    steps,
    results: row.results || null,
    patentTitle: row.patent_title,
    patentNumber: row.patent_number,
    createdAt: toTimestamp(row.created_at),
    updatedAt: toTimestamp(row.updated_at),
  };
}

function getSessionFilePath(sessionId: string): string {
  return path.join(getSessionsDir(), `${sessionId}.json`);
}

function cacheSession(session: AnalysisSession): void {
  memoryStore.set(session.id, session);
}

async function loadSessionFromDb(sessionId: string): Promise<AnalysisSession | null> {
  await ensureDatabaseReady();
  const sessionResult = await pgQuery<SessionRow>(
    `
      select
        s.id,
        s.user_id,
        u.name as user_name,
        s.status,
        s.input_type,
        s.input_value,
        s.file_name,
        s.file_url,
        s.text_content,
        s.patent_title,
        s.patent_number,
        s.results,
        s.created_at,
        s.updated_at
      from analysis_sessions s
      join users u on u.id = s.user_id
      where s.id = $1
      limit 1
    `,
    [sessionId],
  );
  const row = sessionResult.rows[0];
  if (!row) {
    return null;
  }

  const stepsResult = await pgQuery<StepRow>(
    `
      select step_id, step_name, status, error, started_at, completed_at
      from analysis_steps
      where session_id = $1
      order by step_id asc
    `,
    [sessionId],
  );

  const session = mapDbSession(row, stepsResult.rows.map(mapStepRow));
  cacheSession(session);
  return session;
}

export async function createSession(input: AnalysisInput, user: AuthUser): Promise<AnalysisSession> {
  await ensureDatabaseReady();
  const now = Date.now();
  const id = `analysis_${now}_${Math.random().toString(36).slice(2, 8)}`;

  const session: AnalysisSession = {
    id,
    userId: user.id,
    userName: user.name,
    status: 'idle',
    input,
    steps: createDefaultSteps(),
    results: null,
    createdAt: now,
    updatedAt: now,
  };

  await pgQuery(
    `
      insert into analysis_sessions (
        id, user_id, status, input_type, input_value, file_name, file_url, text_content, results
      )
      values ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
    `,
    [
      id,
      user.id,
      session.status,
      input.type,
      input.value || null,
      input.fileName || null,
      input.fileUrl || null,
      input.text || null,
      JSON.stringify({}),
    ],
  );

  await Promise.all(
    session.steps.map((step) =>
      pgQuery(
        `
          insert into analysis_steps (session_id, step_id, step_name, status)
          values ($1, $2, $3, $4)
        `,
        [id, step.id, step.name, step.status],
      ),
    ),
  );

  cacheSession(session);
  return session;
}

export async function updateSessionStatus(sessionId: string, status: AnalysisStatus): Promise<void> {
  await ensureDatabaseReady();
  const session = memoryStore.get(sessionId);
  if (session) {
    session.status = status;
    session.updatedAt = Date.now();
    cacheSession(session);
  }

  await pgQuery(
    `
      update analysis_sessions
      set status = $2, updated_at = now()
      where id = $1
    `,
    [sessionId, status],
  );
}

export async function updateStepStatus(
  sessionId: string,
  stepId: number,
  status: StepStatus,
  error?: string,
): Promise<void> {
  await ensureDatabaseReady();
  const session = memoryStore.get(sessionId);
  const now = Date.now();

  if (session) {
    const step = session.steps.find((item) => item.id === stepId);
    if (step) {
      step.status = status;
      step.error = error;

      if (status === 'running') {
        step.startedAt = now;
        step.completedAt = undefined;
      }

      if (status === 'completed' || status === 'error') {
        step.completedAt = now;
        if (!step.startedAt) {
          step.startedAt = now;
        }
      }

      session.updatedAt = now;
      cacheSession(session);
    }
  }

  await pgQuery(
    `
      update analysis_steps
      set
        status = $3,
        error = $4,
        started_at = case
          when $3 = 'running' and started_at is null then now()
          when $3 = 'running' then started_at
          else started_at
        end,
        completed_at = case
          when $3 in ('completed', 'error') then now()
          when $3 = 'running' then null
          else completed_at
        end,
        updated_at = now()
      where session_id = $1 and step_id = $2
    `,
    [sessionId, stepId, status, error || null],
  );

  await pgQuery('update analysis_sessions set updated_at = now() where id = $1', [sessionId]);
}

export async function updateResults(
  sessionId: string,
  partial: Partial<AnalysisResults>,
  metadata?: { patentTitle?: string | null; patentNumber?: string | null },
): Promise<void> {
  await ensureDatabaseReady();
  const session = memoryStore.get(sessionId);
  let results = partial as AnalysisResults;

  if (session) {
    session.results = { ...(session.results || {}), ...partial } as AnalysisResults;
    if (metadata?.patentTitle !== undefined) {
      session.patentTitle = metadata.patentTitle;
    }
    if (metadata?.patentNumber !== undefined) {
      session.patentNumber = metadata.patentNumber;
    }
    session.updatedAt = Date.now();
    cacheSession(session);
    results = session.results;
  } else {
    const loaded = await loadSessionFromDb(sessionId);
    if (loaded) {
      loaded.results = { ...(loaded.results || {}), ...partial } as AnalysisResults;
      if (metadata?.patentTitle !== undefined) {
        loaded.patentTitle = metadata.patentTitle;
      }
      if (metadata?.patentNumber !== undefined) {
        loaded.patentNumber = metadata.patentNumber;
      }
      loaded.updatedAt = Date.now();
      cacheSession(loaded);
      results = loaded.results || (partial as AnalysisResults);
    }
  }

  await pgQuery(
    `
      update analysis_sessions
      set
        results = $2::jsonb,
        patent_title = coalesce($3, patent_title),
        patent_number = coalesce($4, patent_number),
        updated_at = now()
      where id = $1
    `,
    [
      sessionId,
      JSON.stringify(results || {}),
      metadata?.patentTitle ?? null,
      metadata?.patentNumber ?? null,
    ],
  );
}

export function getSession(sessionId: string): AnalysisSession | null {
  return memoryStore.get(sessionId) || null;
}

export async function getSessionAsync(sessionId: string): Promise<AnalysisSession | null> {
  try {
    const dbSession = await loadSessionFromDb(sessionId);
    if (dbSession) {
      return dbSession;
    }

    const cached = memoryStore.get(sessionId);
    if (cached) {
      return cached;
    }

    const raw = await readFile(getSessionFilePath(sessionId), 'utf-8');
    const session = JSON.parse(raw) as AnalysisSession;
    memoryStore.set(sessionId, session);
    return session;
  } catch {
    return memoryStore.get(sessionId) || null;
  }
}

export async function listSessionsForUser(user: AuthUser): Promise<AnalysisSession[]> {
  await ensureDatabaseReady();
  const result = await pgQuery<SessionRow>(
    `
      select
        s.id,
        s.user_id,
        u.name as user_name,
        s.status,
        s.input_type,
        s.input_value,
        s.file_name,
        s.file_url,
        s.text_content,
        s.patent_title,
        s.patent_number,
        s.results,
        s.created_at,
        s.updated_at
      from analysis_sessions s
      join users u on u.id = s.user_id
      ${user.role === 'admin' ? '' : 'where s.user_id = $1'}
      order by s.created_at desc
    `,
    user.role === 'admin' ? [] : [user.id],
  );

  const sessions = result.rows.map((row) => mapDbSession(row, []));
  sessions.forEach((session) => cacheSession(session));
  return sessions;
}
