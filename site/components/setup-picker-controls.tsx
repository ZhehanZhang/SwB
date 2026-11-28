'use client';

import { Check, Copy } from 'lucide-react';
import { useRef, useState } from 'react';
import { combineSetupPrompts, setupChoices, type SetupChoice, type SetupSources } from '@/lib/setup-picker';
import type { setupPickerCopy } from '@/lib/setup-picker-copy';
import { personalizeSwb } from '@/lib/urls';
import { MarkTile } from './mark-tile';
import { useSwbAddress } from './swb-address-provider';

type Props = {
  labels: (typeof setupPickerCopy)[keyof typeof setupPickerCopy];
  sources: SetupSources;
};

export function SetupPickerControls({ labels, sources }: Props) {
  const preview = useRef<HTMLTextAreaElement>(null);
  const [selection, setSelection] = useState<SetupChoice[]>([]);
  const [status, setStatus] = useState<'idle' | 'copied' | 'error'>('idle');
  const { address } = useSwbAddress();
  const prompt = personalizeSwb(combineSetupPrompts(sources, selection), address);

  return (
    <div data-setup-picker className="not-prose min-w-0">
      <fieldset className="grid min-w-0 grid-cols-2 gap-2 min-[600px]:grid-cols-3">
        <legend className="sr-only">{labels.apps}</legend>
        {setupChoices.map(({ id, name, mark }) => (
          <label key={id} className="flex min-h-11 cursor-pointer items-center gap-2 rounded-md border px-2 text-sm hover:bg-fd-muted has-[:checked]:border-fd-primary has-[:checked]:bg-fd-muted">
            <input type="checkbox" checked={selection.includes(id)} className="size-4 shrink-0 accent-fd-primary"
              onChange={({ currentTarget }) => {
                const { checked } = currentTarget;
                setSelection((previous) => checked ? [...previous, id] : previous.filter((choice) => choice !== id));
                setStatus('idle');
                if (preview.current) preview.current.scrollTop = 0;
              }} />
            <MarkTile kind={mark} className="swb-picker-mark" />
            <span>{name}</span>
          </label>
        ))}
      </fieldset>
      <textarea ref={preview} aria-label={labels.prompt} lang={prompt ? 'en' : undefined}
        placeholder={labels.prompt} value={prompt} readOnly
        className="mt-4 block h-44 w-full field-sizing-fixed resize-none overflow-y-auto rounded-md border bg-fd-background px-3 py-2 font-mono text-[13px] leading-relaxed sm:h-52" />
      <div className="mt-3 flex">
        <button type="button" disabled={!prompt} className="swb-button swb-button-outline disabled:opacity-50"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(prompt);
              setStatus('copied');
            } catch {
              preview.current?.select();
              setStatus('error');
            }
          }}>
          {status === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {labels.copy}
        </button>
        <span className="sr-only" role="status">
          {status === 'copied' ? labels.copied : status === 'error' ? labels.copyError : ''}
        </span>
      </div>
    </div>
  );
}
