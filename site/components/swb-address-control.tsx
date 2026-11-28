'use client';

import { Popover, PopoverContent, PopoverTrigger } from 'fumadocs-ui/components/ui/popover';
import { Link as LinkIcon } from 'lucide-react';
import { useId } from 'react';
import { isGetStartedAddressPath, linkWithSwbAddress } from '@/lib/address';
import { getStartedCopy } from '@/lib/get-started-copy';
import type { Locale } from '@/lib/i18n';
import { CopyTextButton } from './prompt-actions';
import { SwbAddressField } from './swb-address-field';
import { useSwbAddress } from './swb-address-provider';

const labels = {
  en: { clear: 'Clear', copy: 'Copy link with your address' },
  'zh-Hans': { clear: '清除', copy: '复制含地址的链接' },
  'zh-Hant': { clear: '清除', copy: '複製含位址的連結' },
  es: { clear: 'Borrar', copy: 'Copiar enlace con tu dirección' },
  fr: { clear: 'Effacer', copy: 'Copier le lien avec votre adresse' },
  de: { clear: 'Leeren', copy: 'Link mit Ihrer Adresse kopieren' },
  ko: { clear: '지우기', copy: '내 주소가 포함된 링크 복사' },
  ja: { clear: 'クリア', copy: 'アドレス入りのリンクをコピー' },
};

function removeLegacyAddress() {
  const url = new URL(location.href);
  if (!isGetStartedAddressPath(url.pathname) || !url.searchParams.has('url')) return;
  url.searchParams.delete('url');
  history.replaceState(history.state, '', url);
}

export function SwbAddressControl({ lang, show }: { lang: Locale; show: boolean }) {
  const { address, saveAddress } = useSwbAddress();
  const fieldId = useId();
  const copy = getStartedCopy[lang];
  const text = labels[lang as keyof typeof labels] ?? labels.en;
  const host = address ? new URL(address).host : '';

  return (
    <div className="w-18 shrink-0 min-[360px]:w-23 lg:w-36">
      {show && address && (
        <Popover>
          <PopoverTrigger type="button" aria-label={copy.address} title={host}
            className="flex h-9 w-full items-center gap-1.5 rounded-md px-1.5 text-xs text-fd-muted-foreground hover:bg-fd-muted hover:text-fd-foreground">
            <LinkIcon aria-hidden="true" className="size-4 shrink-0" />
            <span className="truncate">{host}</span>
          </PopoverTrigger>
          <PopoverContent align="end" aria-label={copy.address} collisionPadding={12}
            className="w-72 max-w-[calc(100vw-2rem)] bg-fd-popover p-3 motion-reduce:animate-none">
            <label className="font-medium" htmlFor={fieldId}>{copy.address}</label>
            <SwbAddressField id={fieldId} label={copy.address} error={copy.addressError} onEdit={(next) => {
              if (next) removeLegacyAddress();
            }} className="mt-1.5 h-10 w-full rounded-md border bg-fd-background px-2.5 text-base aria-invalid:border-red-500" />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-1">
              <button type="button" onClick={() => {
                removeLegacyAddress();
                saveAddress(null);
              }} className="swb-button swb-button-compact hover:bg-fd-muted">{text.clear}</button>
              <CopyTextButton text={() => linkWithSwbAddress(location.href, address)}
                labels={{ copy: text.copy, copied: copy.copied }} className="swb-button swb-button-compact hover:bg-fd-muted" />
            </div>
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}
