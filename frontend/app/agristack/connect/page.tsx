'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAppDispatch } from '../../hooks/useRedux';
import { setToken } from '../../store/tokenSlice';
import {
  AgriStackLoginForm,
  AgriStackLoginShell,
} from '../../components/AgriStackLoginForm';
import { hasAgriStackSession } from '../../lib/agristackSession';

function ConnectContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const dispatch = useAppDispatch();
  const [checking, setChecking] = useState(true);

  const nextPath = (() => {
    const raw = (searchParams.get('next') || '/dashboard').trim();
    return raw.startsWith('/') ? raw : '/dashboard';
  })();

  useEffect(() => {
    if (hasAgriStackSession()) {
      router.replace(nextPath);
      return;
    }
    setChecking(false);
  }, [router, nextPath]);

  if (checking) {
    return (
      <AgriStackLoginShell>
        <p className="relative z-10 text-sm text-stone-500">Checking AgriStack session…</p>
      </AgriStackLoginShell>
    );
  }

  return (
    <AgriStackLoginShell>
      <AgriStackLoginForm
        title="Sign in to AgriStack"
        subtitle="Validate your AgriStack credentials to open Run New Assessment. Credentials are not taken from server defaults — only what you enter here for this browser session."
        onSuccess={(_creds, token) => {
          if (token.access_token) {
            dispatch(
              setToken({
                access_token: token.access_token,
                token_type: token.token_type,
                expires_in: token.expires_in,
                refresh_token: token.refresh_token,
              })
            );
          }
          router.replace(nextPath);
        }}
      />
    </AgriStackLoginShell>
  );
}

export default function AgriStackConnectPage() {
  return (
    <Suspense
      fallback={
        <AgriStackLoginShell>
          <p className="relative z-10 text-sm text-stone-500">Loading…</p>
        </AgriStackLoginShell>
      }
    >
      <ConnectContent />
    </Suspense>
  );
}
