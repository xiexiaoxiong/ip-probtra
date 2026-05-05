'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, Loader2 } from 'lucide-react';
import type { AuthUser } from '@/lib/types';
import { PendingUsersTable } from '@/components/admin/pending-users-table';
import { Button } from '@/components/ui/button';

interface AdminUser extends AuthUser {
  approvedByName?: string | null;
}

export default function AdminPage() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [meRes, usersRes] = await Promise.all([fetch('/api/auth/me'), fetch('/api/admin/users')]);
      if (meRes.status === 401 || usersRes.status === 401) {
        window.location.href = '/login';
        return;
      }
      const meData = await meRes.json();
      const usersData = await usersRes.json();
      if (!meRes.ok || !usersRes.ok) {
        throw new Error(meData.error || usersData.error || '加载管理员数据失败');
      }
      setCurrentUser(meData.user);
      setUsers(usersData.users || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载管理员数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pendingUsers = users.filter((user) => user.status === 'pending');
  const approvedUsers = users.filter((user) => user.status !== 'pending');

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/">
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                返回首页
              </Button>
            </Link>
            <span className="text-sm font-medium">管理员后台</span>
          </div>
          <div className="text-xs text-muted-foreground">{currentUser?.email || ''}</div>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            加载中...
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <>
            <PendingUsersTable title="待审批用户" users={pendingUsers} onUpdated={load} />
            <PendingUsersTable title="已处理用户" users={approvedUsers} onUpdated={load} allowActions={false} />
          </>
        )}
      </main>
    </div>
  );
}
