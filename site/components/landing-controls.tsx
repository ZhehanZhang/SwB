'use client';

import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import type { Locale } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';

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
