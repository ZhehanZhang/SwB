'use client';

import { Check, Copy, ExternalLink } from 'lucide-react';
import { useState } from 'react';

type Labels = { copy: string; copied: string; claude: string; chatgpt: string };

export function PromptActions({ text, labels, showOpenLinks }: { text: string; labels: Labels; showOpenLinks: boolean }) {
  const [copied, setCopied] = useState(false);
  const query = encodeURIComponent(text);
  const link = 'swb-button swb-button-compact hover:bg-fd-muted';
  return (
    <div className="flex flex-wrap items-center gap-1 px-2 py-1.5">
      <button type="button" className={link} onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}>
        {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        {labels.copy}
      </button>
      <span className="sr-only" role="status">{copied ? labels.copied : ''}</span>
      {showOpenLinks && (
        <>
          <a className={link} href={`https://claude.ai/new?q=${query}`} target="_blank" rel="noreferrer">
            <ExternalLink aria-hidden="true" />{labels.claude}
          </a>
          <a className={link} href={`https://chatgpt.com/?q=${query}`} target="_blank" rel="noreferrer">
            <ExternalLink aria-hidden="true" />{labels.chatgpt}
          </a>
        </>
      )}
    </div>
  );
}
