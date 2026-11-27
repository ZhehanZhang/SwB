'use client';

import { Check, Copy, ExternalLink } from 'lucide-react';
import { useState } from 'react';

type Labels = { copy: string; copied: string; claude: string; chatgpt: string };

export function PromptActions({ text, labels }: { text: string; labels: Labels }) {
  const [copied, setCopied] = useState(false);
  const query = encodeURIComponent(text);
  const link = 'inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm hover:bg-fd-muted';
  return (
    <div className="flex flex-wrap items-center gap-1 px-2 py-1.5">
      <button type="button" className={link} onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}>
        {copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
        {copied ? labels.copied : labels.copy}
      </button>
      <a className={link} href={`https://claude.ai/new?q=${query}`} target="_blank" rel="noreferrer">
        {labels.claude}<ExternalLink aria-hidden="true" className="size-3.5" />
      </a>
      <a className={link} href={`https://chatgpt.com/?q=${query}`} target="_blank" rel="noreferrer">
        {labels.chatgpt}<ExternalLink aria-hidden="true" className="size-3.5" />
      </a>
    </div>
  );
}
