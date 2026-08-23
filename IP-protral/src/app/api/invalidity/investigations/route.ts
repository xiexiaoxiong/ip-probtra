import { randomUUID } from 'crypto';
import { writeFile } from 'fs/promises';
import path from 'path';
import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import { createSession, updateResults, updateSessionStatus } from '@/lib/analysis-store';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  InvalidityServiceError,
  requestInvalidityService,
  resolveInvalidityUploadPath,
} from '@/lib/invalidity-service';
import {
  InvalidityUploadStorageError,
  prepareInvalidityUploadRoot,
} from '@/lib/invalidity-upload-storage';
import type { AnalysisInput } from '@/lib/types';
import { invalidityAutomaticRounds } from '@/lib/invalidity-contracts';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type CreateBody = {
  type?: 'url' | 'file' | 'text' | 'record';
  url?: string;
  fileKey?: string;
  fileName?: string;
  text?: string;
  patentRecordId?: number;
  declaredCriticalDate?: string;
  maxRounds?: number;
};

function errorResponse(error: unknown): NextResponse {
  if (error instanceof InvalidityServiceError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  if (error instanceof InvalidityUploadStorageError) {
    return NextResponse.json({ error: error.message }, { status: error.status });
  }
  return NextResponse.json(
    { error: error instanceof Error ? error.message : '创建无效调查失败' },
    { status: 500 },
  );
}

async function persistText(text: string): Promise<string> {
  const uploadRoot = await prepareInvalidityUploadRoot(CANONICAL_INVALIDITY_ENVIRONMENT, { create: true });
  const fileKey = `invalidity-${CANONICAL_INVALIDITY_ENVIRONMENT}-${Date.now()}-${randomUUID()}.txt`;
  const destination = path.resolve(uploadRoot, fileKey);
  await writeFile(destination, text, { encoding: 'utf8', flag: 'wx' });
  return resolveInvalidityUploadPath(CANONICAL_INVALIDITY_ENVIRONMENT, fileKey);
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准开展分析' }, { status: 403 });
  }

  let sessionId: string | null = null;
  try {
    await prepareInvalidityUploadRoot(CANONICAL_INVALIDITY_ENVIRONMENT, { create: true });
    const body = (await request.json()) as CreateBody;
    let maxRounds: 1 | 2 | 3 | 4 | 5;
    try {
      maxRounds = invalidityAutomaticRounds(body.maxRounds);
    } catch (error) {
      return NextResponse.json(
        { error: error instanceof Error ? error.message : 'maxRounds 无效' },
        { status: 400 },
      );
    }
    const inputType = body.type;
    let input: AnalysisInput;
    const source: Record<string, unknown> = {};

    if (inputType === 'url' && body.url?.trim()) {
      input = { type: 'url', value: body.url.trim() };
      source.source_url = body.url.trim();
    } else if (inputType === 'file' && body.fileKey?.trim()) {
      const fileKey = body.fileKey.trim();
      const fileLocation = await resolveInvalidityUploadPath(CANONICAL_INVALIDITY_ENVIRONMENT, fileKey);
      input = {
        type: 'file',
        value: fileKey,
        fileName: body.fileName,
      };
      source.source_path = fileLocation;
    } else if (inputType === 'text' && body.text?.trim()) {
      const sourcePath = await persistText(body.text.trim());
      input = { type: 'text', value: body.text.trim(), text: body.text.trim() };
      source.source_path = sourcePath;
    } else if (inputType === 'record' && Number.isInteger(body.patentRecordId) && Number(body.patentRecordId) > 0) {
      input = { type: 'text', value: `patent_record_id:${body.patentRecordId}` };
      source.patent_record_id = Number(body.patentRecordId);
    } else {
      return NextResponse.json({ error: '必须提供一个有效的专利 URL、文件、文本或记录 ID' }, { status: 400 });
    }

    const session = await createSession(input, user, {
      analysisKind: 'invalidity',
      pipelineVersion: 'invalidity-workflow-v1',
    });
    sessionId = session.id;
    await updateSessionStatus(session.id, 'running');

    const created = await requestInvalidityService<Record<string, unknown>>(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      '/v1/investigations',
      {
        method: 'POST',
        timeoutMs: 180_000,
        body: {
          contract_version: 'v1',
          analysis_session_id: session.id,
          ...source,
          patent_number: null,
          declared_critical_date: body.declaredCriticalDate || null,
          max_rounds: maxRounds,
          provider_mode: 'live',
          idempotency_key: `portal-invalidity-${session.id}`,
        },
      },
    );
    const investigationId = String(created.investigation_id || '');
    if (!investigationId) throw new Error('无效检索服务未返回 investigation_id');

    const started = await requestInvalidityService<Record<string, unknown>>(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      `/v1/investigations/${encodeURIComponent(investigationId)}/start`,
      { method: 'POST' },
    );
    await updateResults(session.id, {
      invalidityInvestigationId: investigationId,
      invalidityStatus: String(started.status || created.status || 'queued'),
      invalidityReportVersion: 'v1',
    });
    return NextResponse.json({
      sessionId: session.id,
      investigationId,
      status: started.status || created.status || 'queued',
    });
  } catch (error) {
    if (sessionId) {
      await updateSessionStatus(sessionId, 'error').catch(() => undefined);
      await updateResults(sessionId, { invalidityStatus: 'failed' }).catch(() => undefined);
    }
    return errorResponse(error);
  }
}
