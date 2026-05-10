import Link from 'next/link';
import { Suspense } from 'react';
import { Shield } from 'lucide-react';
import { AuthPageGuard } from '@/components/auth/auth-page-guard';
import { LoginForm } from '@/components/auth/login-form';

function LoginFormWithSuspense() {
  return (
    <Suspense fallback={<div className="text-center text-muted-foreground">Loading...</div>}>
      <LoginForm />
    </Suspense>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-muted/30 flex items-center justify-center px-6">
      <AuthPageGuard />
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center gap-2 font-semibold text-lg">
            <Shield className="h-5 w-5 text-primary" />
            IP-Probtra 专利侵权自动识别系统
          </div>
          <p className="text-sm text-muted-foreground">登录后可发起分析并查看历史结果</p>
        </div>
        <LoginFormWithSuspense />
        <p className="text-center text-sm text-muted-foreground">
          没有账号？
          {' '}
          <Link href="/register" className="text-primary underline">
            先去注册
          </Link>
        </p>
      </div>
    </div>
  );
}
