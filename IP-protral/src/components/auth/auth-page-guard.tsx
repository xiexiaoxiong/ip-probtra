'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export function AuthPageGuard() {
  const router = useRouter();

  useEffect(() => {
    let disposed = false;

    async function checkCurrentUser() {
      try {
        const response = await fetch('/api/auth/me', {
          cache: 'no-store',
        });

        if (!response.ok) {
          return;
        }

        const data = await response.json();
        if (!disposed && data.user) {
          router.replace('/');
        }
      } catch {
        // Ignore auth probe failures on auth pages and keep the form usable.
      }
    }

    void checkCurrentUser();

    return () => {
      disposed = true;
    };
  }, [router]);

  return null;
}
