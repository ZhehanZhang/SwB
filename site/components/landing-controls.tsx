'use client';

import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import type { Locale } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';

export function CopyInstall({ lang }: { lang: Locale }) {
  const copy = landingCopy[lang];
  const [status, setStatus] = useState<'idle' | 'copied' | 'error'>('idle');

  return (
    <div className="inline-flex shrink-0 items-center gap-1" data-install-chip>
      <code className="border-b pb-0.5 font-mono text-sm leading-5 whitespace-nowrap">
        <span aria-hidden="true" className="text-fd-muted-foreground">$ </span>{copy.install}
      </code>
      <button type="button" className="swb-button swb-button-icon size-7 min-h-0 text-fd-muted-foreground hover:bg-fd-muted hover:text-fd-foreground"
        title={copy.copy} aria-label={copy.copy} onClick={async () => {
          try {
            await navigator.clipboard.writeText(copy.install);
            setStatus('copied');
          } catch {
            setStatus('error');
          }
        }}>
        {status === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </button>
      <span className="sr-only" role="status">
        {status === 'copied' ? copy.copied : status === 'error' ? copy.copyError : ''}
      </span>
    </div>
  );
}
