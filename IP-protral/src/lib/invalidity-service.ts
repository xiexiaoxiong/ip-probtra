import 'server-only';

import {
  exactLoopbackServiceUrl,
  redactInvalidityPrivatePaths,
  type CriticalDateConfirmationRequest,
  type CriticalDateConfirmationResponse,
  type DocumentDateConfirmationRequest,
  type EvidenceImportRequest,
  type HumanReviewCommandResponse,
  type InvalidityEnvironment,
  type InvalidityReviewContext,
  type InvestigationContinuationRequest,
  type InvestigationContinuationResponse,
} from '@/lib/invalidity-contracts';
import {
  InvalidityUploadStorageError,
  resolveInvalidityUploadPath as resolveUploadPath,
} from '@/lib/invalidity-upload-storage';

export type { InvalidityEnvironment } from '@/lib/invalidity-contracts';

// “test” is a legacy storage/configuration label. Product traffic and the
// eleven-stage module lab now intentionally share this one canonical backend.
export const CANONICAL_INVALIDITY_ENVIRONMENT: InvalidityEnvironment = 'test';

type InvalidityRequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: unknown;
  timeoutMs?: number;
  headers?: Record<string, string>;
};

export class InvalidityServiceError extends Error {
  status: number;
  details: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'InvalidityServiceError';
    this.status = status;
    this.details = details;
  }
}

export function invalidityServiceErrorCode(error: InvalidityServiceError): string | undefined {
  if (!error.details || typeof error.details !== 'object' || Array.isArray(error.details)) return undefined;
  const code = String((error.details as Record<string, unknown>).code || '').trim();
  return /^[A-Z][A-Z0-9_]{1,63}$/.test(code) ? code : undefined;
}

function environmentVariable(environment: InvalidityEnvironment, suffix: 'API_URL' | 'API_TOKEN'): string {
  return `INVALIDITY_${environment.toUpperCase()}_${suffix}`;
}

export function invalidityBaseUrl(environment: InvalidityEnvironment): string {
  const variableName = environmentVariable(environment, 'API_URL');
  const configured = environment === CANONICAL_INVALIDITY_ENVIRONMENT
    ? process.env.INVALIDITY_API_URL || process.env[variableName]
    : process.env[variableName];
  try {
    return exactLoopbackServiceUrl(
      configured,
      environment === CANONICAL_INVALIDITY_ENVIRONMENT ? 'INVALIDITY_API_URL' : variableName,
      environment === 'test' ? 5209 : 5109,
    );
  } catch (error) {
    throw new InvalidityServiceError(
      `${error instanceof Error ? error.message : `${variableName} 配置无效`}，已拒绝跨环境或非本机请求`,
      503,
    );
  }
}

function safePath(pathname: string): string {
  const normalized = pathname.startsWith('/') ? pathname : `/${pathname}`;
  if (normalized === '/health' || normalized.startsWith('/v1/')) {
    return normalized;
  }
  throw new InvalidityServiceError('不允许代理该无效检索服务路径', 400);
}

function invalidityAuthorizationToken(
  environment: InvalidityEnvironment,
): string {
  const tokenVariable = environmentVariable(environment, 'API_TOKEN');
  const token = String(
    environment === CANONICAL_INVALIDITY_ENVIRONMENT
      ? process.env.INVALIDITY_API_TOKEN || process.env[tokenVariable] || ''
      : process.env[tokenVariable] || '',
  ).trim();
  if (!token) {
    throw new InvalidityServiceError(
      `缺少 ${environment === CANONICAL_INVALIDITY_ENVIRONMENT ? 'INVALIDITY_API_TOKEN' : tokenVariable}，禁止无鉴权请求`,
      503,
    );
  }
  return token;
}

export async function requestInvalidityService<T = Record<string, unknown>>(
  environment: InvalidityEnvironment,
  pathname: string,
  options: InvalidityRequestOptions = {},
): Promise<T> {
  const method = options.method || 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  headers.Authorization = `Bearer ${invalidityAuthorizationToken(environment)}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  for (const [name, value] of Object.entries(options.headers || {})) {
    if (name.toLowerCase() === 'authorization' || name.toLowerCase() === 'content-type') continue;
    headers[name] = value;
  }

  let response: Response;
  try {
    response = await fetch(`${invalidityBaseUrl(environment)}${safePath(pathname)}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
      signal: AbortSignal.timeout(options.timeoutMs || 60_000),
    });
  } catch (error) {
    throw new InvalidityServiceError(
      `无效检索服务不可达: ${error instanceof Error ? error.name : 'network error'}`,
      502,
    );
  }

  const text = await response.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: '服务返回非 JSON 响应' };
    }
  }
  if (!response.ok) {
    const object = data && typeof data === 'object' ? data as Record<string, unknown> : {};
    const message = String(object.detail || object.error || `HTTP ${response.status}`);
    throw new InvalidityServiceError(message, response.status, data);
  }
  return redactInvalidityPrivatePaths(data) as T;
}

export async function requestInvalidityServiceBinary(
  environment: InvalidityEnvironment,
  pathname: string,
  timeoutMs = 60_000,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${invalidityBaseUrl(environment)}${safePath(pathname)}`, {
      method: 'GET',
      headers: {
        Accept: 'image/png,image/jpeg,image/tiff',
        Authorization: `Bearer ${invalidityAuthorizationToken(environment)}`,
      },
      cache: 'no-store',
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    throw new InvalidityServiceError(
      `无效检索服务不可达: ${error instanceof Error ? error.name : 'network error'}`,
      502,
    );
  }
  if (!response.ok) {
    const message = await response.text().catch(() => '');
    throw new InvalidityServiceError(
      message ? '目标专利附图读取失败' : `HTTP ${response.status}`,
      response.status,
    );
  }
  return response;
}

export function confirmInvalidityCriticalDate(
  environment: InvalidityEnvironment,
  investigationId: string,
  body: CriticalDateConfirmationRequest,
): Promise<CriticalDateConfirmationResponse> {
  return requestInvalidityService<CriticalDateConfirmationResponse>(
    environment,
    `/v1/investigations/${encodeURIComponent(investigationId)}/critical-date-confirmations`,
    { method: 'POST', body },
  );
}

export function continueInvalidityInvestigation(
  environment: InvalidityEnvironment,
  investigationId: string,
  body: InvestigationContinuationRequest,
): Promise<InvestigationContinuationResponse> {
  return requestInvalidityService<InvestigationContinuationResponse>(
    environment,
    `/v1/investigations/${encodeURIComponent(investigationId)}/continuations`,
    { method: 'POST', body },
  );
}

function humanReviewPath(investigationId: string, action: string): string {
  return `/v1/investigations/${encodeURIComponent(investigationId)}/human-reviews/${action}`;
}

export function getInvalidityReviewContext(
  environment: InvalidityEnvironment,
  investigationId: string,
): Promise<InvalidityReviewContext> {
  return requestInvalidityService<InvalidityReviewContext>(
    environment,
    `/v1/investigations/${encodeURIComponent(investigationId)}/review-context`,
  );
}

export function importInvalidityEvidence(
  environment: InvalidityEnvironment,
  investigationId: string,
  body: EvidenceImportRequest,
  actor: string,
): Promise<HumanReviewCommandResponse> {
  return requestInvalidityService<HumanReviewCommandResponse>(
    environment,
    humanReviewPath(investigationId, 'evidence-imports'),
    { method: 'POST', body, timeoutMs: 180_000, headers: { 'X-Invalidity-Actor': actor } },
  );
}

export function confirmInvalidityDocumentDate(
  environment: InvalidityEnvironment,
  investigationId: string,
  body: DocumentDateConfirmationRequest,
  actor: string,
): Promise<HumanReviewCommandResponse> {
  return requestInvalidityService<HumanReviewCommandResponse>(
    environment,
    humanReviewPath(investigationId, 'document-date-confirmations'),
    { method: 'POST', body, headers: { 'X-Invalidity-Actor': actor } },
  );
}

export async function resolveInvalidityUploadPath(
  environment: InvalidityEnvironment,
  fileKey: unknown,
): Promise<string> {
  try {
    return await resolveUploadPath(environment, fileKey);
  } catch (error) {
    if (error instanceof InvalidityUploadStorageError) {
      throw new InvalidityServiceError(error.message, error.status);
    }
    throw new InvalidityServiceError(
      error instanceof Error ? error.message : '上传文件解析失败',
      500,
    );
  }
}
