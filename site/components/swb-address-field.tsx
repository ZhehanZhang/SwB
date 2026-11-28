'use client';

import { useId, useState } from 'react';
import { validSwbOrigin } from '@/lib/address';
import { exampleSwbOrigins } from '@/lib/urls';
import { useSwbAddress } from './swb-address-provider';

export function SwbAddressField({ id, label, error, describedBy, className, onEdit }: {
  id?: string;
  label: string;
  error: string;
  describedBy?: string;
  className?: string;
  onEdit?: (address: string | null) => void;
}) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const { address, saveAddress } = useSwbAddress();
  const [draft, setDraft] = useState<{ text: string; source: string | null } | null>(null);
  const [check, setCheck] = useState(false);
  const value = draft?.source === address ? draft.text : address ?? '';
  const invalid = Boolean(check && value && !validSwbOrigin(value));

  return (
    <input id={fieldId} aria-label={label} aria-describedby={describedBy} className={className}
      type="text" inputMode="url" value={value} placeholder={exampleSwbOrigins[0]} autoComplete="url"
      spellCheck={false} aria-invalid={invalid || undefined} aria-description={invalid ? error : undefined}
      onChange={({ currentTarget }) => setDraft({ text: currentTarget.value, source: address })}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
      onBlur={() => {
        if (draft?.source !== address) return;
        setCheck(true);
        const next = validSwbOrigin(value);
        if (next) {
          saveAddress(next);
          setDraft(null);
        }
        onEdit?.(next);
      }}
    />
  );
}
