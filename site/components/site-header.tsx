'use client';

import { usePathname } from 'fumadocs-core/framework';
import { Popover, PopoverClose, PopoverContent, PopoverTrigger } from 'fumadocs-ui/components/ui/popover';
import { LanguageSelect } from 'fumadocs-ui/layouts/shared/slots/language-select';
import { FullSearchTrigger, SearchTrigger } from 'fumadocs-ui/layouts/shared/slots/search-trigger';
import { Languages, Menu } from 'lucide-react';
import Link from 'next/link';
import { isAddressDocsPath } from '@/lib/address';
import { localize, type Locale } from '@/lib/i18n';
import { headerLinks, navLabels } from '@/lib/layout.shared';
import { Brand } from './brand';
import { GitHubMark } from './github-mark';
import { SiteContainer } from './site-container';
import { SwbAddressControl } from './swb-address-control';

const iconButton = 'grid size-9 shrink-0 place-items-center rounded-md text-fd-muted-foreground hover:bg-fd-muted hover:text-fd-foreground';
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
          <div data-header-item="search-icon" className="flex lg:hidden"><SearchTrigger className={iconButton} /></div>
          <FullSearchTrigger data-header-item="search" className="hidden h-9 w-48 lg:inline-flex" />
          <SwbAddressControl lang={lang} show={isAddressDocsPath(path)} />
          <LanguageSelect data-header-item="language" className={iconButton}><Languages aria-hidden="true" className="size-4.5" /></LanguageSelect>
          <a data-header-item="github" className={`swb-button swb-button-icon ${iconButton}`} aria-label="GitHub" href={repository} target="_blank" rel="noreferrer">
            <GitHubMark />
          </a>
          <Popover>
            <PopoverTrigger data-header-item="menu" type="button" className={`${iconButton} sm:hidden`} aria-label={navLabels[lang].menu}>
              <Menu aria-hidden="true" className="size-5" />
            </PopoverTrigger>
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
