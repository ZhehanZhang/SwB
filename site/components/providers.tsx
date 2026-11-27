'use client';

import { RootProvider } from 'fumadocs-ui/provider/next';
import type { I18nProviderProps } from 'fumadocs-ui/contexts/i18n';
import type { ReactNode } from 'react';
import { locales } from '@/lib/i18n';

export function Providers({ i18n, children }: { i18n: I18nProviderProps; children: ReactNode }) {
  const onLocaleChange = (next: string) => {
    document.cookie = `swb-lang=${next}; path=/; max-age=31536000; samesite=lax`;
    const rest = window.location.pathname.replace(new RegExp(`^/(${locales.join('|')})(?=/|$)`), '') || '/';
    window.location.assign(next === 'en' ? rest : `/${next}${rest === '/' ? '' : rest}`);
  };
  return <RootProvider i18n={{ ...i18n, onLocaleChange }}>{children}</RootProvider>;
}
