'use client';

import { useId, useState, type ReactNode } from 'react';
import { validSwbOrigin } from '@/lib/address';
import { exampleSwbOrigins } from '@/lib/urls';
import { useSwbAddress } from './swb-address-provider';

export function SwbAddressField({ id, label, error, describedBy, saveLabel, onEdit, children }: {
  id?: string;
  label: string;
  error: string;
  describedBy?: string;
  saveLabel?: string;
  onEdit?: (address: string | null) => void;
  children?: ReactNode;
}) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const { address, saveAddress } = useSwbAddress();
  const [draft, setDraft] = useState<{ text: string; source: string | null } | null>(null);
  const [check, setCheck] = useState(false);
  const value = draft?.source === address ? draft.text : address ?? '';
  const invalid = Boolean(check && value && !validSwbOrigin(value));

  function commit() {
    setCheck(true);
    const next = validSwbOrigin(value);
    if (next) {
      saveAddress(next);
      setDraft(null);
    }
    onEdit?.(next);
  }

  const input = (
    <input id={fieldId} aria-label={label} aria-describedby={describedBy}
      className="min-h-10 w-full min-w-0 rounded-lg border bg-fd-background px-3 text-base font-normal aria-invalid:border-(--destructive)"
      type="text" inputMode="url" value={value} placeholder={exampleSwbOrigins[0]} autoComplete="url"
      spellCheck={false} aria-invalid={invalid || undefined} aria-description={invalid ? error : undefined}
      onChange={({ currentTarget }) => setDraft({ text: currentTarget.value, source: address })}
      onKeyDown={(event) => {
        if (!saveLabel && event.key === 'Enter') event.currentTarget.blur();
      }}
      onBlur={() => {
        if (!saveLabel && draft?.source === address) commit();
      }}
    />
  );

  if (!saveLabel) return input;

  return (
    <form className="grid gap-3" onSubmit={(event) => {
      event.preventDefault();
      commit();
    }}>
      <div className="grid gap-1.5">
        <label className="font-semibold" htmlFor={fieldId}>{label}</label>
        {input}
      </div>
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-3">
        {children}
        <button className="swb-button swb-button-primary" type="submit">{saveLabel}</button>
      </div>
    </form>
  );
}
