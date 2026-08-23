import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse } from '@/lib/auth';
import {
  InvalidityPortalAccessError,
  requireInvaliditySession,
} from '@/lib/invalidity-portal-access';
import { CANONICAL_INVALIDITY_ENVIRONMENT } from '@/lib/invalidity-service';
import {
  InvalidityUploadReceiptError,
  storeInvalidityEvidenceUpload,
} from '@/lib/invalidity-upload-receipts';
import { InvalidityUploadStorageError } from '@/lib/invalidity-upload-storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  try {
    const { id } = await params;
    const access = await requireInvaliditySession(request, id);
    const form = await request.formData();
    const file = form.get('file');
    if (!(file instanceof File)) {
      return NextResponse.json({ error: '未找到上传文件' }, { status: 400 });
    }
    const receipt = await storeInvalidityEvidenceUpload({
      file,
      userId: access.user.id,
      sessionId: access.session.id,
      environment: CANONICAL_INVALIDITY_ENVIRONMENT,
    });
    return NextResponse.json(receipt, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof InvalidityPortalAccessError) {
      if (error.status === 401) return createUnauthorizedResponse(request);
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    if (error instanceof InvalidityUploadReceiptError || error instanceof InvalidityUploadStorageError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json({ error: '对比材料上传失败' }, { status: 500 });
  }
}
