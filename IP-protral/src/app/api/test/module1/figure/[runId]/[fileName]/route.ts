import path from 'path';
import { readFile } from 'fs/promises';
import { NextRequest, NextResponse } from 'next/server';
import { getUploadsDir } from '@/lib/runtime-paths';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

function getContentType(fileName: string): string {
  const ext = path.extname(fileName).toLowerCase();
  if (ext === '.png') return 'image/png';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.gif') return 'image/gif';
  if (ext === '.webp') return 'image/webp';
  return 'application/octet-stream';
}

function isSafeSegment(value: string): boolean {
  if (!value || value === '.' || value === '..') {
    return false;
  }
  return !value.includes('/') && !value.includes('\\') && !value.includes('\0');
}

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ runId: string; fileName: string }> },
): Promise<NextResponse> {
  const { runId, fileName } = await params;

  if (!isSafeSegment(runId) || !isSafeSegment(fileName)) {
    return NextResponse.json({ error: '非法文件路径' }, { status: 400 });
  }

  const filePath = path.join(getUploadsDir(), 'module1-test-figures', runId, fileName);

  try {
    const buffer = await readFile(filePath);
    return new NextResponse(buffer, {
      headers: {
        'Content-Type': getContentType(fileName),
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return NextResponse.json({ error: '图片不存在' }, { status: 404 });
  }
}
