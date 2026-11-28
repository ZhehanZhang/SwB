'use client';

import { usePathname } from 'fumadocs-core/framework';
import { Popover, PopoverClose, PopoverContent } from 'fumadocs-ui/components/ui/popover';
import { LanguageSelect } from 'fumadocs-ui/layouts/shared/slots/language-select';
import { FullSearchTrigger, SearchTrigger } from 'fumadocs-ui/layouts/shared/slots/search-trigger';
import { Languages, Menu } from 'lucide-react';
import Link from 'next/link';
import { isAddressDocsPath } from '@/lib/address';
import { localize, type Locale } from '@/lib/i18n';
import { headerLinks, navLabels } from '@/lib/layout.shared';
import { Brand } from './brand';
import { GitHubMark } from './github-mark';
import { HeaderControl, headerIconButton } from './header-controls';
import { SiteContainer } from './site-container';
import { SwbAddressControl } from './swb-address-control';

const tab = 'rounded-md px-2.5 py-2 text-sm font-medium whitespace-nowrap text-fd-muted-foreground hover:bg-fd-muted hover:text-fd-foreground aria-[current]:text-fd-foreground';

export function SiteHeader({ lang, repository }: { lang: Locale; repository: string }) {
  const path = usePathname();
  const links = headerLinks(lang);
  const active = links.findLast(({ href }) => path === href || path.startsWith(`${href}/`));

  return (
    <header id="nd-nav" className="sticky top-0 z-30 h-(--swb-header-height) border-b bg-fd-background/95 backdrop-blur-sm">
      <SiteContainer as="nav" className="flex h-full items-center gap-1 sm:gap-4">
        <Link data-header-item="brand" className="inline-flex shrink-0" href={localize(lang, '/')}><Brand name="SwB" /></Link>
        <div className="hidden items-center gap-0.5 sm:flex">
          {links.map(({ id, text, href }) => (
            <Link key={id} data-header-item={id} href={href} className={tab} aria-current={href === active?.href ? 'location' : undefined}>{text}</Link>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1 sm:gap-2">
          <div data-header-item="search-icon" className="flex lg:hidden"><SearchTrigger className={headerIconButton} /></div>
          <FullSearchTrigger data-header-item="search" className="hidden h-9 w-48 lg:inline-flex [&_kbd]:w-6 [&_kbd:first-child]:w-12 [&_kbd:first-child]:text-start [&_kbd:last-child]:text-center" />
          <LanguageSelect data-header-item="language" className={headerIconButton}><Languages aria-hidden="true" className="size-4.5" /></LanguageSelect>
          <a data-header-item="github" className={`swb-button swb-button-icon ${headerIconButton}`} aria-label="GitHub" href={repository} target="_blank" rel="noreferrer">
            <GitHubMark />
          </a>
          <SwbAddressControl lang={lang} show={isAddressDocsPath(path)} />
          <Popover>
            <HeaderControl data-header-item="menu" className="sm:hidden" aria-label={navLabels[lang].menu}>
              <Menu aria-hidden="true" className="size-5" />
            </HeaderControl>
            <PopoverContent align="end" className="grid min-w-36 gap-1 p-1">
              {links.map(({ id, text, href }) => (
                <PopoverClose key={id} asChild>
                  <Link href={href} className={tab} aria-current={href === active?.href ? 'location' : undefined}>{text}</Link>
                </PopoverClose>
              ))}
            </PopoverContent>
          </Popover>
        </div>
      </SiteContainer>
    </header>
  );
}
