'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, Loader2 } from 'lucide-react';
import type { AnalysisSession, AuthUser } from '@/lib/types';
import { HistoryList } from '@/components/history/history-list';
import { Button } from '@/components/ui/button';

export default function HistoryPage() {
  const [sessions, setSessions] = useState<AnalysisSession[]>([]);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [meRes, historyRes] = await Promise.all([
          fetch('/api/auth/me'),
          fetch('/api/history'),
        ]);
        if (meRes.status === 401 || historyRes.status === 401) {
          window.location.href = '/login';
          return;
        }
        const meData = await meRes.json();
        const historyData = await historyRes.json();
        if (!meRes.ok || !historyRes.ok) {
          throw new Error(meData.error || historyData.error || '加载历史失败');
        }
        setUser(meData.user);
        setSessions(historyData.sessions || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载历史失败');
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center gap-3">
          <Link href="/">
            <Button variant="ghost" size="sm" className="gap-1.5">
              <ArrowLeft className="h-4 w-4" />
              返回首页
            </Button>
          </Link>
          <span className="text-sm font-medium">
            {user?.role === 'admin' ? '全站分析历史' : '我的分析历史'}
          </span>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-6 py-8">
        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            加载中...
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <HistoryList sessions={sessions} showUser={user?.role === 'admin'} />
        )}
      </main>
    </div>
  );
}
