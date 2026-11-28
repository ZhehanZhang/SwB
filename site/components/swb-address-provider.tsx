'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { createContext, Suspense, use, useCallback, useLayoutEffect, useState, type ReactNode } from 'react';
import { isGetStartedAddressPath, readSharedAddress, swbAddressKey, validSwbOrigin } from '@/lib/address';
import { isDocsPath } from '@/lib/i18n';

const oldAddressKey = 'swb-get-started-address';
const AddressContext = createContext<{ address: string | null; saveAddress: (address: string | null) => void }>({
  address: null, saveAddress: () => {},
});

export function useSwbAddress() {
  return use(AddressContext);
}

function AddressLocation({ restore }: { restore: () => void }) {
  const path = usePathname();
  const search = useSearchParams();
  useLayoutEffect(restore, [path, search, restore]);
  return null;
}

export function SwbAddressProvider({ children }: { children: ReactNode }) {
  const [address, setAddress] = useState<string | null>(null);
  const saveAddress = useCallback((input: string | null) => {
    const next = validSwbOrigin(input);
    if (input !== null && !next) return;
    try {
      if (next) localStorage.setItem(swbAddressKey, next);
      else localStorage.removeItem(swbAddressKey);
      localStorage.removeItem(oldAddressKey);
    } catch {}
    setAddress(next);
  }, []);

  const restore = useCallback(() => {
    const url = new URL(location.href);
    if (!isDocsPath(url.pathname)) return;
    const start = isGetStartedAddressPath(url.pathname);
    const shared = readSharedAddress(url);
    if (shared) {
      if (start && url.searchParams.has('url')) url.searchParams.delete('url');
      history.replaceState(history.state, '', url);
      saveAddress(shared);
      return;
    }
    if (start && !url.searchParams.has('swb') && !/(?:^#|&)swb=/.test(url.hash)) {
      const legacy = validSwbOrigin(url.searchParams.get('url'));
      if (legacy) saveAddress(legacy);
    }
  }, [saveAddress]);

  useLayoutEffect(() => {
    function loadAddress() {
      try {
        const stored = validSwbOrigin(localStorage.getItem(swbAddressKey));
        const legacy = validSwbOrigin(localStorage.getItem(oldAddressKey));
        if (!stored && legacy) saveAddress(legacy);
        else setAddress(stored);
      } catch {}
    }
    loadAddress();
    restore();
    function storageChanged(event: StorageEvent) {
      if (event.key === swbAddressKey || event.key === null) loadAddress();
    }
    window.addEventListener('storage', storageChanged);
    window.addEventListener('hashchange', restore);
    window.addEventListener('popstate', restore);
    return () => {
      window.removeEventListener('storage', storageChanged);
      window.removeEventListener('hashchange', restore);
      window.removeEventListener('popstate', restore);
    };
  }, [restore, saveAddress]);

  return (
    <AddressContext value={{ address, saveAddress }}>
      <Suspense><AddressLocation restore={restore} /></Suspense>
      {children}
    </AddressContext>
  );
}
