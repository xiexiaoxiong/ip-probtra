'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export interface ErrorReportItem {
  id: number;
  analysisSessionId: string | null;
  userId: number | null;
  userName: string | null;
  userEmail: string | null;
  stepId: number | null;
  stepName: string | null;
  errorMessage: string;
  errorStack: string | null;
  patentText: string | null;
  inputType: string | null;
  inputValue: string | null;
  fileUrl: string | null;
  createdAt: string;
}

function formatTime(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '-';
}

function formatStep(report: ErrorReportItem): string {
  if (report.stepName && report.stepId) {
    return `${report.stepId}. ${report.stepName}`;
  }
  if (report.stepName) {
    return report.stepName;
  }
  if (report.stepId) {
    return String(report.stepId);
  }
  return '-';
}

export function ErrorReportsTable({
  title = '错误上报',
  reports,
}: {
  title?: string;
  reports: ErrorReportItem[];
}) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const displayReports = useMemo(() => reports || [], [reports]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {displayReports.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无错误上报</p>
        ) : (
          displayReports.map((report) => {
            const expanded = expandedId === report.id;
            const sessionLink = report.analysisSessionId ? `/history?highlight=${encodeURIComponent(report.analysisSessionId)}` : null;
            return (
              <div key={report.id} className="rounded-lg border p-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="text-sm font-medium">
                      {sessionLink ? (
                        <Link href={sessionLink} className="underline underline-offset-2">
                          {report.analysisSessionId}
                        </Link>
                      ) : (
                        report.analysisSessionId || '-'
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      时间: {formatTime(report.createdAt)} · 用户: {report.userEmail || report.userName || '-'} · 环节: {formatStep(report)}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setExpandedId(expanded ? null : report.id)}
                  >
                    {expanded ? '收起' : '详情'}
                  </Button>
                </div>

                <div className="text-sm text-destructive whitespace-pre-wrap break-words">{report.errorMessage}</div>

                {expanded ? (
                  <div className="space-y-3">
                    <div className="text-xs text-muted-foreground whitespace-pre-wrap break-words">
                      输入: {report.inputType || '-'} · {report.inputValue || report.fileUrl || '-'}
                    </div>
                    {report.patentText ? (
                      <div className="rounded-md border bg-muted/30 p-3 text-xs whitespace-pre-wrap break-words max-h-80 overflow-auto">
                        {report.patentText}
                      </div>
                    ) : null}
                    {report.errorStack ? (
                      <div className="rounded-md border bg-muted/30 p-3 text-xs whitespace-pre-wrap break-words max-h-80 overflow-auto">
                        {report.errorStack}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}

