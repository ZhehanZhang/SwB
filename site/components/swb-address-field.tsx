'use client';

import { Check, X } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { validSwbOrigin } from '@/lib/address';
import { exampleSwbOrigins } from '@/lib/urls';
import { useSwbAddress } from './swb-address-provider';

export function SwbAddressField({ id, label, error, onEdit, popover }: {
  id?: string;
  label: string;
  error: string;
  onEdit?: (address: string | null) => void;
  popover?: {
    labels: { save: string; saved: string; clear: string };
    clear: () => void;
    copy: ReactNode;
  };
}) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const { address, saveAddress } = useSwbAddress();
  const [draft, setDraft] = useState<{ text: string; source: string | null } | null>(null);
  const [check, setCheck] = useState(false);
  const [saved, setSaved] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const savedTimer = useRef<ReturnType<typeof setTimeout>>(null);
  useEffect(() => () => { if (savedTimer.current) clearTimeout(savedTimer.current); }, []);
  const value = draft?.source === address ? draft.text : address ?? '';
  const invalid = Boolean(check && value && !validSwbOrigin(value));

  function commit() {
    setCheck(true);
    const next = validSwbOrigin(value);
    if (next) {
      saveAddress(next);
      setDraft(null);
      if (popover) {
        setSaved(true);
        if (savedTimer.current) clearTimeout(savedTimer.current);
        savedTimer.current = setTimeout(() => setSaved(false), 1500);
      }
    }
    onEdit?.(next);
  }

  const input = (
    <input ref={inputRef} id={fieldId} aria-label={label}
      className={popover
        ? 'min-w-0 flex-1 rounded-l-md bg-transparent ps-2 pe-0.5 text-base font-normal focus-visible:-outline-offset-2 sm:ps-2.5 sm:pe-1 sm:text-sm'
        : 'min-h-10 w-full min-w-0 rounded-lg border bg-fd-background px-3 text-base font-normal aria-invalid:border-(--destructive)'}
      type="text" inputMode="url" value={value} placeholder={exampleSwbOrigins[0]} autoComplete="url"
      spellCheck={false} aria-invalid={invalid || undefined} aria-description={invalid ? error : undefined}
      onChange={({ currentTarget }) => {
        setDraft({ text: currentTarget.value, source: address });
        if (saved) setSaved(false);
      }}
      onKeyDown={(event) => {
        if (!popover && event.key === 'Enter') event.currentTarget.blur();
      }}
      onBlur={() => {
        if (!popover && draft?.source === address) commit();
      }}
    />
  );

  if (!popover) return input;

  return (
    <form className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1" onSubmit={(event) => {
      event.preventDefault();
      commit();
    }}>
      <label className="col-start-1 row-start-1 ps-1 text-xs font-normal text-fd-muted-foreground" htmlFor={fieldId}>{label}</label>
      <div data-address-field className={`col-span-2 col-start-1 row-start-2 flex h-9 min-w-0 items-stretch rounded-md border bg-fd-background ${invalid ? 'border-(--destructive)' : ''}`}>
        {input}
        {saved && <Check data-address-saved aria-hidden="true" className="size-3 shrink-0 self-center text-fd-muted-foreground sm:size-3.5" />}
        {value && <button type="button" aria-label={popover.labels.clear} onClick={() => {
          setDraft(null);
          setCheck(false);
          setSaved(false);
          popover.clear();
          inputRef.current?.focus({ preventScroll: true });
        }}
          className="my-0.5 ms-0.5 grid w-6 shrink-0 place-items-center rounded-sm text-fd-muted-foreground hover:bg-fd-muted hover:text-fd-foreground focus-visible:-outline-offset-2 sm:me-0.5 sm:w-7">
          <X aria-hidden="true" className="size-3.5" />
        </button>}
        <button type="submit" disabled={!value} className="shrink-0 rounded-r-[5px] border-s bg-fd-muted/55 px-2 text-[13px] font-medium text-fd-muted-foreground enabled:hover:bg-fd-muted enabled:hover:text-fd-foreground focus-visible:-outline-offset-2 disabled:text-fd-muted-foreground/60 sm:px-2.5 sm:text-sm">
          {popover.labels.save}
        </button>
      </div>
      <div className="col-start-2 row-start-1">{popover.copy}</div>
      <span className="sr-only" role="status">{saved ? popover.labels.saved : ''}</span>
    </form>
  );
}
