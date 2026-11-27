'use client';

import { RootProvider } from 'fumadocs-ui/provider/next';
import type { I18nProviderProps } from 'fumadocs-ui/contexts/i18n';
import type { ReactNode } from 'react';
import { isLocale, localeCookie, locales, localize } from '@/lib/i18n';

const localePrefix = new RegExp(`^/(${locales.join('|')})(?=/|$)`);

export function Providers({ i18n, children }: { i18n: I18nProviderProps; children: ReactNode }) {
  const onLocaleChange = (next: string) => {
    if (!isLocale(next)) return;
    document.cookie = `${localeCookie}=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
    const destination = new URL(window.location.href);
    destination.pathname = localize(next, destination.pathname.replace(localePrefix, '') || '/');
    window.location.assign(destination);
  };
  return <RootProvider i18n={{ ...i18n, onLocaleChange }}>{children}</RootProvider>;
}
