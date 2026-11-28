'use client';

import { Popover } from 'fumadocs-ui/components/ui/popover';
import { Link as LinkIcon } from 'lucide-react';
import { useRef } from 'react';
import { isGetStartedAddressPath, linkWithSwbAddress } from '@/lib/address';
import { getStartedCopy } from '@/lib/get-started-copy';
import type { Locale } from '@/lib/i18n';
import { HeaderControl, HeaderPopover } from './header-controls';
import { CopyTextButton } from './prompt-actions';
import { SwbAddressField } from './swb-address-field';
import { useSwbAddress } from './swb-address-provider';

const labels = {
  en: { save: 'Save', saved: 'Address saved', clear: 'Clear address', copy: 'Copy link' },
  'zh-Hans': { save: '保存', saved: '地址已保存', clear: '清除地址', copy: '复制链接' },
  'zh-Hant': { save: '儲存', saved: '網址已儲存', clear: '清除網址', copy: '複製連結' },
  es: { save: 'Guardar', saved: 'Dirección guardada', clear: 'Borrar dirección', copy: 'Copiar enlace' },
  fr: { save: 'Enregistrer', saved: 'Adresse enregistrée', clear: 'Effacer l’adresse', copy: 'Copier le lien' },
  de: { save: 'Speichern', saved: 'Adresse gespeichert', clear: 'Adresse löschen', copy: 'Link kopieren' },
  ko: { save: '저장', saved: '주소 저장 완료', clear: '주소 지우기', copy: '링크 복사' },
  ja: { save: '保存', saved: 'アドレスを保存しました', clear: 'アドレスを消去', copy: 'リンクをコピー' },
};

const previewParam = 'address-popover';

function removeLegacyAddress() {
  const url = new URL(location.href);
  if (!isGetStartedAddressPath(url.pathname) || !url.searchParams.has('url')) return;
  url.searchParams.delete('url');
  history.replaceState(history.state, '', url);
}

export function SwbAddressControl({ lang }: { lang: Locale }) {
  const { address, saveAddress } = useSwbAddress();
  const panel = useRef<HTMLDivElement>(null);
  const copy = getStartedCopy[lang];
  const text = labels[lang];
  const host = address ? new URL(address).host : '';
  const joined = typeof window !== 'undefined' && new URLSearchParams(location.search).get(previewParam) === 'v2';

  return (
    <Popover>
      <HeaderControl data-header-item="address" aria-label={host ? `${copy.address} ${host}` : copy.address} title={host || copy.address}>
        <LinkIcon aria-hidden="true" className="size-4.5" />
      </HeaderControl>
      <HeaderPopover ref={panel} aria-label={copy.address}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          panel.current?.focus({ preventScroll: true });
        }}
        className="w-[calc(100vw-2.5rem)] p-1 sm:w-80 sm:p-2">
        <SwbAddressField label={copy.address} error={copy.addressError} onEdit={(next) => {
          if (next) removeLegacyAddress();
        }} popover={{
          variant: joined ? 'joined' : 'inline',
          labels: text,
          clear: () => {
            removeLegacyAddress();
            saveAddress(null);
          },
          copy: <CopyTextButton text={() => {
            if (!address) return '';
            const url = new URL(location.href);
            url.searchParams.delete(previewParam);
            return linkWithSwbAddress(url.href, address);
          }} labels={{ copy: text.copy, copied: copy.copied }} iconOnly disabled={!address}
            className="grid size-7 place-items-center rounded-md text-fd-muted-foreground enabled:hover:bg-fd-muted enabled:hover:text-fd-foreground disabled:opacity-45 [&_svg]:size-3.5" />,
        }} />
      </HeaderPopover>
    </Popover>
  );
}
