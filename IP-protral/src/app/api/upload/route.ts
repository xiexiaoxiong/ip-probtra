import { mkdir, writeFile } from 'fs/promises';
import path from 'path';
import { randomUUID } from 'crypto';
import { NextRequest, NextResponse } from 'next/server';
import { getUploadsDir } from '@/lib/runtime-paths';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

const ALLOWED_TYPES = [
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
];

const ALLOWED_EXTENSIONS = ['.pdf', '.doc', '.docx', '.txt'];
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function sanitizeFileName(fileName: string): string {
  return fileName.replace(/[^a-zA-Z0-9._-]/g, '_');
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const formData = await request.formData();
    const file = formData.get('file') as File | null;

    if (!file) {
      return NextResponse.json({ error: '未找到上传文件' }, { status: 400 });
    }

    const extension = `.${file.name.split('.').pop()?.toLowerCase() || ''}`;
    if (!ALLOWED_TYPES.includes(file.type) && !ALLOWED_EXTENSIONS.includes(extension)) {
      return NextResponse.json(
        { error: `不支持的文件类型: ${file.type || extension}。仅支持 PDF、DOCX、TXT 格式` },
        { status: 400 },
      );
    }

    if (file.size > MAX_FILE_SIZE) {
      return NextResponse.json({ error: '文件大小超过 50MB 限制' }, { status: 400 });
    }

    const uploadsDir = getUploadsDir();
    await mkdir(uploadsDir, { recursive: true });

    const fileBuffer = Buffer.from(await file.arrayBuffer());
    const sanitizedFileName = sanitizeFileName(file.name);
    const storageKey = `${Date.now()}-${randomUUID()}-${sanitizedFileName}`;
    const absolutePath = path.join(uploadsDir, storageKey);

    await writeFile(absolutePath, fileBuffer);

    return NextResponse.json({
      success: true,
      fileKey: storageKey,
      fileName: file.name,
      fileUrl: absolutePath,
      fileSize: file.size,
    });
  } catch (error) {
    console.error('[Upload API] Error:', error);
    return NextResponse.json(
      { error: `文件上传失败: ${error instanceof Error ? error.message : '未知错误'}` },
      { status: 500 },
    );
  }
}
