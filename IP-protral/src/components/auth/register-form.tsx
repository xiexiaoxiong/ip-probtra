'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export function RegisterForm() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((data as { error?: string }).error || '注册失败');
      }
      setSuccess((data as { message?: string }).message || '注册成功，请等待管理员审批');
      setName('');
      setEmail('');
      setPassword('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>注册账号</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <label className="text-sm font-medium">姓名</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="请输入姓名" />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium">邮箱</label>
          <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="请输入邮箱" />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium">密码</label>
          <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="至少 8 位" />
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {success ? (
          <p className="text-sm text-green-700">
            {success}
            {' '}
            <Link href="/login" className="underline">
              去登录
            </Link>
          </p>
        ) : null}
        <Button className="w-full" onClick={handleSubmit} disabled={submitting || !name || !email || !password}>
          {submitting ? '提交中...' : '提交注册'}
        </Button>
      </CardContent>
    </Card>
  );
}
