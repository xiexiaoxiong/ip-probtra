import type { QueryResultRow } from 'pg';
import { ensureDatabaseReady } from './db-init';
import { pgQuery } from './postgres';

export interface ErrorReportInput {
  analysisSessionId?: string | null;
  userId?: number | null;
  stepId?: number | null;
  stepName?: string | null;
  errorMessage: string;
  errorStack?: string | null;
  patentText?: string | null;
  inputType?: string | null;
  inputValue?: string | null;
  fileUrl?: string | null;
  meta?: Record<string, unknown>;
}

export interface ErrorReportRecord {
  id: number;
  analysisSessionId: string | null;
  userId: number | null;
  userName: string | null;
  userEmail: string | null;
  stepId: number | null;
  stepName: string | null;
  errorMessage: string;
  errorStack: string | null;
  patentText: string | null;
  inputType: string | null;
  inputValue: string | null;
  fileUrl: string | null;
  meta: Record<string, unknown>;
  createdAt: string;
}

function clampText(value: string, maxLen: number): string {
  if (value.length <= maxLen) {
    return value;
  }
  return value.slice(0, maxLen);
}

export async function createErrorReport(input: ErrorReportInput): Promise<number> {
  await ensureDatabaseReady();
  const result = await pgQuery<{ id: number }>(
    `
      insert into error_reports (
        analysis_session_id,
        user_id,
        step_id,
        step_name,
        error_message,
        error_stack,
        patent_text,
        input_type,
        input_value,
        file_url,
        meta
      )
      values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
      returning id
    `,
    [
      input.analysisSessionId ?? null,
      input.userId ?? null,
      input.stepId ?? null,
      input.stepName ?? null,
      clampText(input.errorMessage, 4000),
      input.errorStack ? clampText(input.errorStack, 8000) : null,
      input.patentText ? clampText(input.patentText, 20000) : null,
      input.inputType ?? null,
      input.inputValue ? clampText(input.inputValue, 1000) : null,
      input.fileUrl ? clampText(input.fileUrl, 2000) : null,
      JSON.stringify(input.meta ?? {}),
    ],
  );
  return result.rows[0]?.id ?? 0;
}

interface ErrorReportRow extends QueryResultRow {
  id: number;
  analysis_session_id: string | null;
  user_id: number | null;
  user_name: string | null;
  user_email: string | null;
  step_id: number | null;
  step_name: string | null;
  error_message: string;
  error_stack: string | null;
  patent_text: string | null;
  input_type: string | null;
  input_value: string | null;
  file_url: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
}

export async function listErrorReports(params?: {
  limit?: number;
  offset?: number;
}): Promise<{ reports: ErrorReportRecord[] }> {
  await ensureDatabaseReady();
  const limit = Math.min(Math.max(params?.limit ?? 50, 1), 200);
  const offset = Math.max(params?.offset ?? 0, 0);

  const result = await pgQuery<ErrorReportRow>(
    `
      select
        r.id,
        r.analysis_session_id,
        r.user_id,
        u.name as user_name,
        u.email as user_email,
        r.step_id,
        r.step_name,
        r.error_message,
        r.error_stack,
        r.patent_text,
        r.input_type,
        r.input_value,
        r.file_url,
        r.meta,
        r.created_at
      from error_reports r
      left join users u on u.id = r.user_id
      order by r.created_at desc
      limit $1 offset $2
    `,
    [limit, offset],
  );

  return {
    reports: result.rows.map((row) => ({
      id: row.id,
      analysisSessionId: row.analysis_session_id,
      userId: row.user_id,
      userName: row.user_name,
      userEmail: row.user_email,
      stepId: row.step_id,
      stepName: row.step_name,
      errorMessage: row.error_message,
      errorStack: row.error_stack,
      patentText: row.patent_text,
      inputType: row.input_type,
      inputValue: row.input_value,
      fileUrl: row.file_url,
      meta: row.meta ?? {},
      createdAt: row.created_at,
    })),
  };
}

