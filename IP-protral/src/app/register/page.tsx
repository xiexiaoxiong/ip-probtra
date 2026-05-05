import Link from 'next/link';
import { Shield } from 'lucide-react';
import { RegisterForm } from '@/components/auth/register-form';

export default function RegisterPage() {
  return (
    <div className="min-h-screen bg-muted/30 flex items-center justify-center px-6">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center gap-2 font-semibold text-lg">
            <Shield className="h-5 w-5 text-primary" />
            申请使用账号
          </div>
          <p className="text-sm text-muted-foreground">提交后需要管理员审批，审批通过后才能登录</p>
        </div>
        <RegisterForm />
        <p className="text-center text-sm text-muted-foreground">
          已有账号？
          {' '}
          <Link href="/login" className="text-primary underline">
            返回登录
          </Link>
        </p>
      </div>
    </div>
  );
}
