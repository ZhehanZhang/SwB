'use client';

import { Popover, PopoverContent, PopoverTrigger } from 'fumadocs-ui/components/ui/popover';
import { Link as LinkIcon } from 'lucide-react';
import { useId, useRef } from 'react';
import { isGetStartedAddressPath, linkWithSwbAddress } from '@/lib/address';
import { getStartedCopy } from '@/lib/get-started-copy';
import type { Locale } from '@/lib/i18n';
import { CopyTextButton } from './prompt-actions';
import { SwbAddressField } from './swb-address-field';
import { useSwbAddress } from './swb-address-provider';

const labels = {
  en: { save: 'Save', clear: 'Clear', copy: 'Copy link with your address' },
  'zh-Hans': { save: '保存', clear: '清除', copy: '复制已填好地址的链接' },
  'zh-Hant': { save: '儲存', clear: '清除', copy: '複製已填好網址的連結' },
  es: { save: 'Guardar', clear: 'Borrar', copy: 'Copiar enlace con tu dirección' },
  fr: { save: 'Enregistrer', clear: 'Effacer', copy: 'Copier le lien avec votre adresse' },
  de: { save: 'Speichern', clear: 'Leeren', copy: 'Link mit Ihrer Adresse kopieren' },
  ko: { save: '저장', clear: '지우기', copy: '내 주소가 포함된 링크 복사' },
  ja: { save: '保存', clear: 'クリア', copy: 'アドレス入りのリンクをコピー' },
};

function removeLegacyAddress() {
  const url = new URL(location.href);
  if (!isGetStartedAddressPath(url.pathname) || !url.searchParams.has('url')) return;
  url.searchParams.delete('url');
  history.replaceState(history.state, '', url);
}

export function SwbAddressControl({ lang, show, className }: { lang: Locale; show: boolean; className: string }) {
  const { address, saveAddress } = useSwbAddress();
  const fieldId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const copy = getStartedCopy[lang];
  const text = labels[lang];
  const host = address ? new URL(address).host : '';
  const quietButton = 'swb-button swb-button-compact text-start hover:bg-fd-muted';

  return (
    <div className="w-9 shrink-0">
      {show && address && (
        <Popover>
          <PopoverTrigger data-header-item="address" type="button" aria-label={`${copy.address} ${host}`} title={host} className={className}>
            <LinkIcon aria-hidden="true" className="size-4.5" />
          </PopoverTrigger>
          <PopoverContent ref={panel} align="end" aria-label={copy.address} collisionPadding={8}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              panel.current?.focus({ preventScroll: true });
            }}
            className="grid w-80 max-w-[calc(100vw-1rem)] gap-3 bg-fd-popover p-3 motion-reduce:animate-none">
            <SwbAddressField id={fieldId} label={copy.address} error={copy.addressError} saveLabel={text.save} onEdit={(next) => {
              if (next) removeLegacyAddress();
            }} />
            <div className="flex items-stretch gap-2 border-t pt-3">
              <button type="button" onClick={() => {
                removeLegacyAddress();
                saveAddress(null);
              }} className={quietButton}>{text.clear}</button>
              <CopyTextButton text={() => linkWithSwbAddress(location.href, address)}
                labels={{ copy: text.copy, copied: copy.copied }} className={quietButton} showIcon={false} />
            </div>
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}
