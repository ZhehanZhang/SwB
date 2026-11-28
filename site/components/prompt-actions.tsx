'use client';

import { Check, Copy, ExternalLink } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { openUrl, type Target } from '@/lib/setup';
import { personalizeSwb } from '@/lib/urls';
import { useSwbAddress } from './swb-address-provider';

const targetNames: Record<Target, string> = { claude: 'Claude', chatgpt: 'ChatGPT', grok: 'Grok' };
type CopyLabels = { copy: string; copied: string };
type Labels = CopyLabels & { open: string };

export function CopyTextButton({ text, labels, className, showIcon = true }: { text: string | (() => string | Promise<string>); labels: CopyLabels; className: string; showIcon?: boolean }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return (
    <>
      <button type="button" className={className} onClick={async () => {
        const content = typeof text === 'string' ? text : text();
        try {
          if (typeof content === 'string') await navigator.clipboard.writeText(content);
          else await navigator.clipboard.write([new ClipboardItem({ 'text/plain': content })]);
        } catch {
          return;
        }
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1800);
      }}>
        {showIcon && (copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />)}
        {labels.copy}
      </button>
      <span className="sr-only" role="status">{copied ? labels.copied : ''}</span>
    </>
  );
}

export function PromptActions({ text, labels, targets }: { text: string; labels: Labels; targets: readonly Target[] }) {
  const link = 'swb-button swb-button-compact hover:bg-fd-muted';
  return (
    <div className="flex flex-wrap items-center gap-1 px-2 py-1.5">
      <CopyTextButton text={text} labels={labels} className={link} />
      {targets.map((target) => (
        <a key={target} className={link} href={openUrl[target](text)} target="_blank" rel="noreferrer">
          <ExternalLink aria-hidden="true" />{labels.open.replace('{app}', targetNames[target])}
        </a>
      ))}
    </div>
  );
}

export function SetupPromptContent({ text, labels, targets }: { text: string; labels: Labels; targets: readonly Target[] }) {
  const { address } = useSwbAddress();
  const prompt = personalizeSwb(text, address);
  return (
    <>
      <PromptActions text={prompt} labels={labels} targets={targets} />
      <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap wrap-anywhere border-t px-4 py-3.5 font-mono text-[13px] leading-relaxed">{prompt}</pre>
    </>
  );
}
