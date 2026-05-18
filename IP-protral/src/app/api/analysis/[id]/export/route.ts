import { NextRequest, NextResponse } from 'next/server';
import { getSessionAsync } from '@/lib/analysis-store';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { withPgClient } from '@/lib/postgres';
import { buildAnalysisReportWorkbook } from '@/lib/analysis-report-export';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

function sanitizeFileName(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return 'analysis-report';
  }

  return trimmed
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, '-')
    .slice(0, 80);
}

function encodeContentDisposition(fileName: string): string {
  return `attachment; filename="${fileName}"; filename*=UTF-8''${encodeURIComponent(fileName)}`;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return createUnauthorizedResponse(request);
  }

  const { id } = await params;
  const session = await getSessionAsync(id);
  if (!session) {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权导出该分析报告' }, { status: 403 });
  }

  try {
    const workbook = await withPgClient((client) => buildAnalysisReportWorkbook(client, session));
    const fileBuffer = await workbook.xlsx.writeBuffer();
    const patentTitle = session.results?.patent?.title || session.patentTitle || 'analysis-report';
    const fileName = `${sanitizeFileName(patentTitle)}-${session.id}.xlsx`;

    return new NextResponse(fileBuffer, {
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': encodeContentDisposition(fileName),
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : '导出报告失败';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
