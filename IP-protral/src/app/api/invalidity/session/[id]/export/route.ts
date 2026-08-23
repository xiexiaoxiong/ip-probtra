import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync } from '@/lib/analysis-store';
import { buildInvalidityWorkbook } from '@/lib/invalidity-report-export';
import { invalidityReportPendingMessage } from '@/lib/invalidity-contracts';
import {
  CANONICAL_INVALIDITY_ENVIRONMENT,
  InvalidityServiceError,
  requestInvalidityService,
} from '@/lib/invalidity-service';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session || session.analysisKind !== 'invalidity') {
    return NextResponse.json({ error: '无效调查会话不存在' }, { status: 404 });
  }
  if (!isAdmin(user) && session.userId !== user.id) {
    return NextResponse.json({ error: '无权导出该调查' }, { status: 403 });
  }
  const investigationId = String(session.results?.invalidityInvestigationId || '');
  if (!investigationId) return NextResponse.json({ error: '调查尚未建立' }, { status: 409 });

  try {
    const report = await requestInvalidityService<Record<string, unknown>>(
      CANONICAL_INVALIDITY_ENVIRONMENT,
      `/v1/investigations/${encodeURIComponent(investigationId)}/report-data`,
    );
    const pendingMessage = invalidityReportPendingMessage(report);
    if (pendingMessage) {
      return NextResponse.json({ error: pendingMessage, code: 'REPORT_PENDING' }, { status: 202 });
    }
    const buffer = await buildInvalidityWorkbook(report);
    const body = Uint8Array.from(buffer).buffer;
    return new NextResponse(body, {
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': `attachment; filename="invalidity-${encodeURIComponent(id)}.xlsx"`,
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    if (error instanceof InvalidityServiceError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '导出无效检索报告失败' },
      { status: 422 },
    );
  }
}
