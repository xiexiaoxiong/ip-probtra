import { createHash } from 'crypto';

export type InvalidityTestProxyOperation =
  | { kind: 'health' }
  | { kind: 'create_investigation' }
  | { kind: 'start_investigation'; investigationId: string }
  | { kind: 'read_investigation'; investigationId: string }
  | { kind: 'read_report_data'; investigationId: string }
  | { kind: 'read_review_context'; investigationId: string }
  | { kind: 'import_evidence'; investigationId: string }
  | { kind: 'confirm_document_date'; investigationId: string }
  | { kind: 'create_module_run' }
  | { kind: 'read_module_run'; moduleRunId: string }
  | { kind: 'cancel_module_run'; moduleRunId: string }
  | { kind: 'retry_module_run'; moduleRunId: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isInvalidityTestResourceId(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value);
}

/**
 * The test proxy is an application API, not a transparent tunnel. Keep this
 * allowlist deliberately small and expand it only with an ownership rule.
 */
export function invalidityTestProxyOperation(
  method: string,
  path: readonly string[],
): InvalidityTestProxyOperation | null {
  if (method === 'GET' && path.length === 1 && path[0] === 'health') {
    return { kind: 'health' };
  }
  if (
    method === 'POST'
    && path.length === 2
    && path[0] === 'v1'
    && path[1] === 'investigations'
  ) {
    return { kind: 'create_investigation' };
  }
  if (
    method === 'POST'
    && path.length === 4
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
    && path[3] === 'start'
  ) {
    return { kind: 'start_investigation', investigationId: path[2] };
  }
  if (
    method === 'GET'
    && path.length === 3
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
  ) {
    return { kind: 'read_investigation', investigationId: path[2] };
  }
  if (
    method === 'GET'
    && path.length === 4
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
    && path[3] === 'report-data'
  ) {
    return { kind: 'read_report_data', investigationId: path[2] };
  }
  if (
    method === 'GET'
    && path.length === 4
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
    && path[3] === 'review-context'
  ) {
    return { kind: 'read_review_context', investigationId: path[2] };
  }
  if (
    method === 'POST'
    && path.length === 5
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
    && path[3] === 'human-reviews'
    && path[4] === 'evidence-imports'
  ) {
    return { kind: 'import_evidence', investigationId: path[2] };
  }
  if (
    method === 'POST'
    && path.length === 5
    && path[0] === 'v1'
    && path[1] === 'investigations'
    && isInvalidityTestResourceId(path[2])
    && path[3] === 'human-reviews'
    && path[4] === 'document-date-confirmations'
  ) {
    return { kind: 'confirm_document_date', investigationId: path[2] };
  }
  if (
    method === 'POST'
    && path.length === 3
    && path[0] === 'v1'
    && path[1] === 'lab'
    && path[2] === 'module-runs'
  ) {
    return { kind: 'create_module_run' };
  }
  if (
    method === 'GET'
    && path.length === 4
    && path[0] === 'v1'
    && path[1] === 'lab'
    && path[2] === 'module-runs'
    && isInvalidityTestResourceId(path[3])
  ) {
    return { kind: 'read_module_run', moduleRunId: path[3] };
  }
  if (
    method === 'POST'
    && path.length === 5
    && path[0] === 'v1'
    && path[1] === 'lab'
    && path[2] === 'module-runs'
    && isInvalidityTestResourceId(path[3])
    && path[4] === 'cancel'
  ) {
    return { kind: 'cancel_module_run', moduleRunId: path[3] };
  }
  if (
    method === 'POST'
    && path.length === 5
    && path[0] === 'v1'
    && path[1] === 'lab'
    && path[2] === 'module-runs'
    && isInvalidityTestResourceId(path[3])
    && path[4] === 'retries'
  ) {
    return { kind: 'retry_module_run', moduleRunId: path[3] };
  }
  return null;
}

export function scopedInvalidityTestIdentifier(
  purpose:
    | 'investigation-session'
    | 'investigation-key'
    | 'module-key'
    | 'module-retry-key',
  userId: number,
  clientValue: unknown,
): string {
  const value = String(clientValue || '').trim();
  if (!Number.isSafeInteger(userId) || userId <= 0 || !value) {
    throw new Error('测试资源标识缺失');
  }
  const digest = createHash('sha256')
    .update(`${purpose}\0${userId}\0${value}`, 'utf8')
    .digest('hex');
  return `portal-test-u${userId}-${purpose}-${digest}`;
}
