import 'server-only';

import { createHash, randomUUID } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { stat, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { QueryResultRow } from 'pg';
import { ensureDatabaseReady } from '@/lib/db-init';
import type { InvalidityEnvironment, InvalidityEvidenceUploadReceipt } from '@/lib/invalidity-contracts';
import {
  prepareInvalidityUploadRoot,
  resolveInvalidityUploadPath,
} from '@/lib/invalidity-upload-storage';
import { pgQuery } from '@/lib/postgres';

const RECEIPT_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_EVIDENCE_FILE_SIZE = 50 * 1024 * 1024;
const EVIDENCE_EXTENSIONS = new Set(['.pdf']);

interface ReceiptRow extends QueryResultRow {
  id: string;
  user_id: number;
  analysis_session_id: string;
  environment: InvalidityEnvironment;
  purpose: 'evidence_import';
  file_key: string;
  file_name: string;
  byte_size: string | number;
  sha256: string;
  mime_type: string;
  reserved_idempotency_key_sha256: string | null;
  consumed_at: string | null;
  upstream_action_id: string | null;
  expires_at: string;
}

export type ClaimedInvalidityUploadReceipt = {
  receiptId: string;
  filePath: string;
  fileName: string;
  byteSize: number;
  sha256: string;
  mimeType: string;
  idempotencyKeySha256: string;
  alreadyConsumed: boolean;
};

export class InvalidityUploadReceiptError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'InvalidityUploadReceiptError';
    this.status = status;
  }
}

function keyDigest(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function safeFileName(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-180) || 'prior-art-file';
}

function verifiedMimeType(file: File, extension: string, buffer: Buffer): string {
  const declared = file.type.trim().toLowerCase();
  const generic = !declared || declared === 'application/octet-stream';
  if (
    extension !== '.pdf'
    || !buffer.subarray(0, 5).equals(Buffer.from('%PDF-'))
    || (!generic && declared !== 'application/pdf')
  ) {
    throw new InvalidityUploadReceiptError('仅接受扩展名、MIME 和文件签名一致的 PDF', 400);
  }
  return 'application/pdf';
}

function validOwnerInput(userId: number, sessionId: string): boolean {
  return Number.isSafeInteger(userId) && userId > 0 && sessionId.trim().length > 0;
}

function publicReceipt(row: ReceiptRow): InvalidityEvidenceUploadReceipt {
  return {
    contract_version: 'v1',
    upload_receipt_id: row.id,
    file_name: row.file_name,
    byte_size: Number(row.byte_size),
    sha256: row.sha256,
    mime_type: row.mime_type,
    expires_at: new Date(row.expires_at).toISOString(),
  };
}

async function verifyFrozenReceiptFile(filePath: string, row: ReceiptRow): Promise<void> {
  const expectedSize = Number(row.byte_size);
  const status = await stat(filePath);
  if (
    !status.isFile()
    || status.size !== expectedSize
    || expectedSize <= 0
    || expectedSize > MAX_EVIDENCE_FILE_SIZE
    || row.mime_type !== 'application/pdf'
  ) {
    throw new InvalidityUploadReceiptError('上传文件与冻结收据不一致，请重新上传', 409);
  }

  const digest = createHash('sha256');
  const stream = createReadStream(filePath);
  let byteSize = 0;
  let signature = Buffer.alloc(0);
  try {
    for await (const chunk of stream) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      byteSize += bytes.length;
      if (byteSize > expectedSize || byteSize > MAX_EVIDENCE_FILE_SIZE) {
        stream.destroy();
        throw new InvalidityUploadReceiptError('上传文件大小在收据签发后发生变化', 409);
      }
      if (signature.length < 5) {
        signature = Buffer.concat([signature, bytes.subarray(0, 5 - signature.length)]);
      }
      digest.update(bytes);
    }
  } catch (error) {
    stream.destroy();
    throw error;
  }
  if (
    byteSize !== expectedSize
    || digest.digest('hex') !== row.sha256
    || !signature.equals(Buffer.from('%PDF-'))
  ) {
    throw new InvalidityUploadReceiptError('上传文件与冻结收据不一致，请重新上传', 409);
  }
}

export async function createInvalidityUploadReceipt(input: {
  userId: number;
  sessionId: string;
  environment: InvalidityEnvironment;
  fileKey: string;
  fileName: string;
  byteSize: number;
  sha256: string;
  mimeType: string;
}): Promise<InvalidityEvidenceUploadReceipt> {
  if (!validOwnerInput(input.userId, input.sessionId)) {
    throw new InvalidityUploadReceiptError('上传收据缺少有效用户或会话', 400);
  }
  if (!Number.isSafeInteger(input.byteSize) || input.byteSize <= 0) {
    throw new InvalidityUploadReceiptError('上传文件大小无效', 400);
  }
  if (!/^[a-f0-9]{64}$/.test(input.sha256)) {
    throw new InvalidityUploadReceiptError('上传文件摘要无效', 400);
  }
  await ensureDatabaseReady();
  const id = randomUUID();
  const expiresAt = new Date(Date.now() + RECEIPT_TTL_MS);
  const result = await pgQuery<ReceiptRow>(
    `
      insert into invalidity_upload_receipts (
        id, user_id, analysis_session_id, environment, purpose,
        file_key, file_name, byte_size, sha256, mime_type, expires_at
      ) values ($1, $2, $3, $4, 'evidence_import', $5, $6, $7, $8, $9, $10)
      returning *
    `,
    [
      id,
      input.userId,
      input.sessionId,
      input.environment,
      input.fileKey,
      input.fileName,
      input.byteSize,
      input.sha256,
      input.mimeType,
      expiresAt.toISOString(),
    ],
  );
  return publicReceipt(result.rows[0]);
}

export async function storeInvalidityEvidenceUpload(input: {
  file: File;
  userId: number;
  sessionId: string;
  environment: InvalidityEnvironment;
}): Promise<InvalidityEvidenceUploadReceipt> {
  const extension = path.extname(input.file.name).toLowerCase();
  if (!EVIDENCE_EXTENSIONS.has(extension)) {
    throw new InvalidityUploadReceiptError('当前人工材料导入仅支持可可靠生成文本和页图的 PDF', 400);
  }
  if (input.file.size <= 0 || input.file.size > MAX_EVIDENCE_FILE_SIZE) {
    throw new InvalidityUploadReceiptError('文件必须大于 0 且不超过 50MB', 400);
  }
  const buffer = Buffer.from(await input.file.arrayBuffer());
  const mimeType = verifiedMimeType(input.file, extension, buffer);
  const sha256 = createHash('sha256').update(buffer).digest('hex');
  const root = await prepareInvalidityUploadRoot(input.environment, { create: true });
  const fileKey = `invalidity-${input.environment}-${randomUUID()}-${safeFileName(input.file.name)}`;
  const destination = path.resolve(root, fileKey);
  if (path.dirname(destination) !== root) {
    throw new InvalidityUploadReceiptError('上传目标路径无效', 400);
  }
  await writeFile(destination, buffer, { flag: 'wx', mode: 0o600 });
  try {
    return await createInvalidityUploadReceipt({
      userId: input.userId,
      sessionId: input.sessionId,
      environment: input.environment,
      fileKey,
      fileName: input.file.name,
      byteSize: buffer.length,
      sha256,
      mimeType,
    });
  } catch (error) {
    await unlink(destination).catch(() => undefined);
    throw error;
  }
}

export async function claimInvalidityUploadReceipt(input: {
  receiptId: string;
  userId: number;
  sessionId: string;
  environment: InvalidityEnvironment;
  idempotencyKey: string;
}): Promise<ClaimedInvalidityUploadReceipt> {
  const receiptId = input.receiptId.trim();
  const idempotencyKey = input.idempotencyKey.trim();
  if (!receiptId || !validOwnerInput(input.userId, input.sessionId) || idempotencyKey.length < 8) {
    throw new InvalidityUploadReceiptError('上传收据或幂等键无效', 400);
  }
  await ensureDatabaseReady();
  const digest = keyDigest(idempotencyKey);
  const result = await pgQuery<ReceiptRow>(
    `
      update invalidity_upload_receipts
      set reserved_idempotency_key_sha256 = $5
      where id = $1
        and user_id = $2
        and analysis_session_id = $3
        and environment = $4
        and purpose = 'evidence_import'
        and expires_at > now()
        and (
          reserved_idempotency_key_sha256 is null
          or reserved_idempotency_key_sha256 = $5
        )
        and (
          consumed_at is null
          or reserved_idempotency_key_sha256 = $5
        )
      returning *
    `,
    [receiptId, input.userId, input.sessionId, input.environment, digest],
  );
  const row = result.rows[0];
  if (!row) {
    const visible = await pgQuery<Pick<ReceiptRow, 'reserved_idempotency_key_sha256' | 'expires_at'>>(
      `
        select reserved_idempotency_key_sha256, expires_at
        from invalidity_upload_receipts
        where id = $1 and user_id = $2 and analysis_session_id = $3 and environment = $4
        limit 1
      `,
      [receiptId, input.userId, input.sessionId, input.environment],
    );
    const owned = visible.rows[0];
    if (!owned) throw new InvalidityUploadReceiptError('上传收据不存在', 404);
    if (new Date(owned.expires_at).getTime() <= Date.now()) {
      throw new InvalidityUploadReceiptError('上传收据已过期，请重新上传', 410);
    }
    throw new InvalidityUploadReceiptError('上传收据已绑定另一项提交意图', 409);
  }
  const filePath = await resolveInvalidityUploadPath(input.environment, row.file_key);
  await verifyFrozenReceiptFile(filePath, row);
  return {
    receiptId: row.id,
    filePath,
    fileName: row.file_name,
    byteSize: Number(row.byte_size),
    sha256: row.sha256,
    mimeType: row.mime_type,
    idempotencyKeySha256: digest,
    alreadyConsumed: Boolean(row.consumed_at),
  };
}

export async function consumeInvalidityUploadReceipt(input: {
  receiptId: string;
  userId: number;
  sessionId: string;
  environment: InvalidityEnvironment;
  idempotencyKeySha256: string;
  upstreamActionId?: string;
}): Promise<void> {
  const result = await pgQuery(
    `
      update invalidity_upload_receipts
      set consumed_at = coalesce(consumed_at, now()),
          upstream_action_id = coalesce(upstream_action_id, $6)
      where id = $1
        and user_id = $2
        and analysis_session_id = $3
        and environment = $4
        and reserved_idempotency_key_sha256 = $5
    `,
    [
      input.receiptId,
      input.userId,
      input.sessionId,
      input.environment,
      input.idempotencyKeySha256,
      input.upstreamActionId || null,
    ],
  );
  if (result.rowCount !== 1) {
    throw new InvalidityUploadReceiptError('上传收据消费状态冲突', 409);
  }
}
