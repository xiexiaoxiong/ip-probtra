'use client';

import Link from 'next/link';
import type { AnalysisSession } from '@/lib/types';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { getSessionDisplayInfo } from '@/lib/utils';

function formatTime(value: number): string {
  return new Date(value).toLocaleString('zh-CN');
}

export function HistoryList({
  sessions,
  showUser,
}: {
  sessions: AnalysisSession[];
  showUser?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>分析历史</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无分析记录</p>
        ) : (
          sessions.map((session) => {
            const display = getSessionDisplayInfo(session);

            return (
              <div key={session.id} className="rounded-lg border p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="space-y-1">
                    <div className="font-medium">{display.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {display.patentNumber ? `${display.patentNumber} · ` : ''}
                      {formatTime(session.createdAt)}
                      {showUser && session.userName ? ` · 用户: ${session.userName}` : ''}
                    </div>
                  </div>
                  <Badge variant="outline">{session.status}</Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link href={`/results?session=${session.id}`}>
                    <Button size="sm">查看结果</Button>
                  </Link>
                  <Link href={`/database?session=${session.id}`}>
                    <Button size="sm" variant="outline">查看数据库</Button>
                  </Link>
                </div>
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
