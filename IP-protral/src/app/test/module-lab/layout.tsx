import type { ReactNode } from 'react';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AUTH_COOKIE_NAME, getCurrentUserBySessionToken, isAdmin } from '@/lib/auth';

export const dynamic = 'force-dynamic';

export default async function ModuleLabLayout({ children }: { children: ReactNode }) {
  const cookieStore = await cookies();
  const user = await getCurrentUserBySessionToken(cookieStore.get(AUTH_COOKIE_NAME)?.value || null);
  if (!user) redirect('/login?redirect=/test/module-lab');
  if (user.status !== 'approved' || !isAdmin(user)) redirect('/');
  return children;
}
