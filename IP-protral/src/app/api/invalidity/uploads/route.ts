import { randomUUID } from 'crypto';
import { writeFile } from 'fs/promises';
import path from 'path';
import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import { CANONICAL_INVALIDITY_ENVIRONMENT } from '@/lib/invalidity-service';
import {
  InvalidityUploadStorageError,
  prepareInvalidityUploadRoot,
} from '@/lib/invalidity-upload-storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

const ALLOWED_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
]);
const ALLOWED_EXTENSIONS = new Set(['.pdf', '.doc', '.docx', '.txt']);
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function safeName(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-180) || 'patent-file';
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准上传文件' }, { status: 403 });
  }
  const environment = CANONICAL_INVALIDITY_ENVIRONMENT;
  try {
    const form = await request.formData();
    const file = form.get('file');
    if (!(file instanceof File)) return NextResponse.json({ error: '未找到上传文件' }, { status: 400 });
    const extension = path.extname(file.name).toLowerCase();
    if (!ALLOWED_TYPES.has(file.type) && !ALLOWED_EXTENSIONS.has(extension)) {
      return NextResponse.json({ error: '仅支持 PDF、DOC、DOCX、TXT 文件' }, { status: 400 });
    }
    if (file.size <= 0 || file.size > MAX_FILE_SIZE) {
      return NextResponse.json({ error: '文件必须大于 0 且不超过 50MB' }, { status: 400 });
    }
    const uploadRoot = await prepareInvalidityUploadRoot(environment, { create: true });
    const fileKey = `invalidity-${environment}-${Date.now()}-${randomUUID()}-${safeName(file.name)}`;
    const destination = path.resolve(uploadRoot, fileKey);
    if (path.dirname(destination) !== uploadRoot) {
      return NextResponse.json({ error: '上传目标路径无效' }, { status: 400 });
    }
    await writeFile(destination, Buffer.from(await file.arrayBuffer()), { flag: 'wx' });
    return NextResponse.json({
      success: true,
      fileKey,
      fileName: file.name,
      fileSize: file.size,
      environment,
    });
  } catch (error) {
    if (error instanceof InvalidityUploadStorageError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '无效检索文件上传失败' }, { status: 500 });
  }
}
