'use client';

import { FrameworkProvider, type Framework } from 'fumadocs-core/framework';
import { RootProvider } from 'fumadocs-ui/provider/base';
import type { SearchProviderProps } from 'fumadocs-ui/contexts/search';
import type { I18nProviderProps } from 'fumadocs-ui/contexts/i18n';
import Image from 'next/image';
import Link from 'next/link';
import { useParams, usePathname, useRouter } from 'next/navigation';
import type { ReactNode } from 'react';
import { docsVersionAtPath, latestDocsVersion } from '@/lib/docs-version';
import { isLocale, localeCookie, locales, localize } from '@/lib/i18n';
import { SwbAddressProvider } from './swb-address-provider';

const localePrefix = new RegExp(`^/(${locales.join('|')})(?=/|$)`);
const search: SearchProviderProps = {
  hotKey: [
    {
      key: (event) => event.metaKey || event.ctrlKey,
      display: <><span className="swb-key-mac">⌘</span><span className="swb-key-other">Ctrl</span></>,
    },
    { key: 'k', display: 'K' },
  ],
};

function useCanonicalPathname() {
  return usePathname().replace(/^\/en(?=\/|$)/, '') || '/';
}

export function Providers({ i18n, children }: { i18n: I18nProviderProps; children: ReactNode }) {
  const docsVersion = docsVersionAtPath(useCanonicalPathname());
  const onLocaleChange = (next: string) => {
    if (!isLocale(next)) return;
    document.cookie = `${localeCookie}=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
    const destination = new URL(window.location.href);
    destination.pathname = localize(next, destination.pathname.replace(localePrefix, '') || '/');
    window.location.assign(destination);
  };
  return (
    <FrameworkProvider Image={Image as Framework['Image']} Link={Link as Framework['Link']}
      useParams={useParams} usePathname={useCanonicalPathname} useRouter={useRouter}>
      <RootProvider i18n={{ ...i18n, onLocaleChange }}
        search={docsVersion === latestDocsVersion ? search : { ...search, options: { api: `/api/search?version=${docsVersion}` } }}>
        <SwbAddressProvider>{children}</SwbAddressProvider>
      </RootProvider>
    </FrameworkProvider>
  );
}
