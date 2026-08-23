import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import {
  invalidityTestResourceOwnership,
  InvalidityTestResourceNotFoundError,
} from '@/lib/invalidity-test-resource-ownership';
import {
  InvalidityUploadReceiptError,
  storeInvalidityEvidenceUpload,
} from '@/lib/invalidity-upload-receipts';
import { InvalidityUploadStorageError } from '@/lib/invalidity-upload-storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export async function POST(request: NextRequest): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 });
  }
  try {
    const form = await request.formData();
    const investigationId = String(form.get('investigationId') || '').trim();
    const file = form.get('file');
    if (!(file instanceof File)) {
      return NextResponse.json({ error: '未找到上传文件' }, { status: 400 });
    }
    await invalidityTestResourceOwnership().require(
      'investigation',
      investigationId,
      user.id,
    );
    const receipt = await storeInvalidityEvidenceUpload({
      file,
      userId: user.id,
      sessionId: `test-investigation:${investigationId}`,
      environment: 'test',
    });
    return NextResponse.json(receipt, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof InvalidityTestResourceNotFoundError) {
      return NextResponse.json({ error: '测试资源不存在' }, { status: 404 });
    }
    if (error instanceof InvalidityUploadReceiptError || error instanceof InvalidityUploadStorageError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '测试对比材料上传失败' }, { status: 500 });
  }
}
