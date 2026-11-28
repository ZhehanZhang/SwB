'use client';

import { Popover } from 'fumadocs-ui/components/ui/popover';
import { Link as LinkIcon } from 'lucide-react';
import { useId, useRef } from 'react';
import { isGetStartedAddressPath, linkWithSwbAddress } from '@/lib/address';
import { getStartedCopy } from '@/lib/get-started-copy';
import type { Locale } from '@/lib/i18n';
import { HeaderControl, HeaderPopover } from './header-controls';
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

export function SwbAddressControl({ lang, show }: { lang: Locale; show: boolean }) {
  const { address, saveAddress } = useSwbAddress();
  const fieldId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const copy = getStartedCopy[lang];
  const text = labels[lang];
  const host = address ? new URL(address).host : '';
  const quietButton = 'swb-button min-w-0 justify-self-start px-0 text-start text-fd-muted-foreground hover:text-fd-foreground hover:underline hover:underline-offset-4';

  return (
    <div className="w-9 shrink-0">
      {show && address && (
        <Popover>
          <HeaderControl data-header-item="address" aria-label={`${copy.address} ${host}`} title={host}>
            <LinkIcon aria-hidden="true" className="size-4.5" />
          </HeaderControl>
          <HeaderPopover ref={panel} aria-label={copy.address}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              panel.current?.focus({ preventScroll: true });
            }}
            className="w-96 p-3">
            <SwbAddressField id={fieldId} label={copy.address} error={copy.addressError} saveLabel={text.save} onEdit={(next) => {
              if (next) removeLegacyAddress();
            }}>
              <button type="button" onClick={() => {
                removeLegacyAddress();
                saveAddress(null);
              }} className={quietButton}>{text.clear}</button>
              <CopyTextButton text={() => linkWithSwbAddress(location.href, address)}
                labels={{ copy: text.copy, copied: copy.copied }} className={quietButton} showIcon={false} />
            </SwbAddressField>
          </HeaderPopover>
        </Popover>
      )}
    </div>
  );
}
