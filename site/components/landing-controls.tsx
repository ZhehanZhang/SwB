'use client';

import { Check, Copy, Globe, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useState, useSyncExternalStore } from 'react';
import { locales, localize, type Locale } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';

export function LandingLanguage({ lang }: { lang: Locale }) {
  const copy = landingCopy[lang];
  return (
    <label className="relative inline-flex h-9 items-center rounded-md hover:bg-fd-muted">
      <Globe aria-hidden="true" className="pointer-events-none absolute left-2 size-4" />
      <select
        aria-label={copy.language}
        className="h-full cursor-pointer rounded-md pl-8 pr-1 text-sm"
        value={lang}
        onChange={(event) => {
          const choice = locales.find((locale) => locale === event.currentTarget.value);
          if (choice) window.location.assign(localize(choice, '/'));
        }}
      >
        {locales.map((locale) => <option key={locale} value={locale}>{copy.languages[locale]}</option>)}
      </select>
    </label>
  );
}

const subscribe = () => () => {};

export function LandingTheme({ lang }: { lang: Locale }) {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(subscribe, () => true, () => false);
  const dark = mounted && resolvedTheme === 'dark';
  const label = landingCopy[lang].theme[dark ? 'light' : 'dark'];

  return (
    <button type="button" className="grid size-9 place-items-center rounded-md hover:bg-fd-muted"
      aria-label={label} title={label} onClick={() => setTheme(dark ? 'light' : 'dark')}>
      {dark ? <Sun aria-hidden="true" className="size-4" /> : <Moon aria-hidden="true" className="size-4" />}
    </button>
  );
}

export function CopyInstall({ lang }: { lang: Locale }) {
  const copy = landingCopy[lang];
  const [status, setStatus] = useState<'idle' | 'copied' | 'error'>('idle');

  return (
    <div className="inline-flex min-h-11 max-w-full items-center gap-3 rounded-md border bg-fd-muted pl-4 pr-1.5">
      <code className="min-w-0 text-sm">{copy.install}</code>
      <button type="button" className="grid size-8 shrink-0 place-items-center rounded-sm hover:bg-fd-background"
        title={copy.copy} aria-label={copy.copy} onClick={async () => {
          try {
            await navigator.clipboard.writeText(copy.install);
            setStatus('copied');
          } catch {
            setStatus('error');
          }
        }}>
        {status === 'copied' ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
      </button>
      <span className="sr-only" role="status">
        {status === 'copied' ? copy.copied : status === 'error' ? copy.copyError : ''}
      </span>
    </div>
  );
}
