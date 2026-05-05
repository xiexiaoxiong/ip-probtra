'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface AdminUser {
  id: number;
  name: string;
  email: string;
  role: 'admin' | 'user';
  status: 'pending' | 'approved' | 'rejected' | 'disabled';
  approvedAt?: string | null;
  createdAt?: string | null;
  approvedByName?: string | null;
}

function formatTime(value?: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '-';
}

export function PendingUsersTable({
  users,
  onUpdated,
  title = '用户审批',
  allowActions = true,
}: {
  users: AdminUser[];
  onUpdated: () => Promise<void>;
  title?: string;
  allowActions?: boolean;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  async function updateUser(id: number, action: 'approve' | 'reject') {
    setBusyId(id);
    try {
      const response = await fetch(`/api/admin/users/${id}/${action}`, {
        method: 'POST',
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((data as { error?: string }).error || '操作失败');
      }
      await onUpdated();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {users.length === 0 ? (
          <p className="text-sm text-muted-foreground">当前没有待处理用户</p>
        ) : (
          users.map((user) => (
            <div key={user.id} className="rounded-lg border p-4 space-y-3">
              <div className="space-y-1">
                <div className="font-medium">{user.name}</div>
                <div className="text-sm text-muted-foreground">{user.email}</div>
                <div className="text-xs text-muted-foreground">
                  状态: {user.status} · 创建时间: {formatTime(user.createdAt)} · 审批人: {user.approvedByName || '-'}
                </div>
              </div>
              {allowActions ? (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => updateUser(user.id, 'approve')} disabled={busyId === user.id}>
                    通过
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => updateUser(user.id, 'reject')} disabled={busyId === user.id}>
                    拒绝
                  </Button>
                </div>
              ) : null}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
