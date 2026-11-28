'use client';

import { usePathname } from 'fumadocs-core/framework';
import { Popover, PopoverClose } from 'fumadocs-ui/components/ui/popover';
import { ChevronDown } from 'lucide-react';
import Link from 'next/link';
import { docsRoot, docsVersionAtPath, latestDocsVersion } from '@/lib/docs-version';
import { isDocsPath, localize, type Locale } from '@/lib/i18n';
import { headerLinks } from '@/lib/layout.shared';
import { HeaderControl, HeaderPopover } from './header-controls';
import { SiteHeader } from './site-header';

const labels = {
  en: { version: 'Docs version', latest: 'latest' },
  'zh-Hans': { version: '文档版本', latest: '最新版' },
  'zh-Hant': { version: '文件版本', latest: '最新版' },
  es: { version: 'Versión de la documentación', latest: 'actual' },
  fr: { version: 'Version de la documentation', latest: 'actuelle' },
  de: { version: 'Dokumentationsversion', latest: 'aktuell' },
  ko: { version: '문서 버전', latest: '최신' },
  ja: { version: 'ドキュメントのバージョン', latest: '最新版' },
} satisfies Record<Locale, { version: string; latest: string }>;

export function DocsSiteHeader({ lang, repository, versions }: {
  lang: Locale;
  repository: string;
  versions: { version: string; pages: Record<string, string> }[];
}) {
  const path = usePathname();
  const version = docsVersionAtPath(path);
  const current = versions.find((entry) => entry.version === version);
  if (!isDocsPath(path) || !current) return <SiteHeader lang={lang} repository={repository} />;

  const root = localize(lang, docsRoot(version));
  const slug = path.slice(root.length + 1);
  const links = headerLinks(lang).map((link) => ({
    ...link,
    href: link.id === 'docs' ? root : link.id === 'setup' ? current.pages.setup ?? link.href : link.href,
  }));
  const options = versions.map(({ version, pages }) => ({
    version,
    href: Object.hasOwn(pages, slug) ? pages[slug] : localize(lang, docsRoot(version)),
  }));
  return <SiteHeader lang={lang} repository={repository} links={links}
    docsOnlyControl={<DocsVersionControl lang={lang} version={version} options={options} />} />;
}

function DocsVersionControl({ lang, version, options }: {
  lang: Locale;
  version: string;
  options: { version: string; href: string }[];
}) {
  const label = labels[lang];
  const row = 'flex w-full items-center justify-between gap-6 rounded-md px-2 py-1.5 text-start tabular-nums hover:bg-fd-muted focus-visible:bg-fd-muted';

  return (
    <Popover>
      <HeaderControl data-header-item="version" className="w-16 text-sm font-medium tabular-nums"
        aria-label={`${label.version} ${version}`}>
        <span className="flex min-w-0 items-center gap-1">
          <span className="truncate">{version}</span>
          <ChevronDown className="size-3.5 shrink-0" aria-hidden="true" />
        </span>
      </HeaderControl>
      <HeaderPopover aria-label={label.version} className="grid w-36 min-w-0 p-1 text-sm">
        {options.map(({ version: option, href }) => {
          const content = <><span>{option}</span>{option === latestDocsVersion && <span className="text-xs text-fd-muted-foreground">{label.latest}</span>}</>;
          return (
            <PopoverClose key={option} asChild>
              {option === version
                ? <button type="button" className={`${row} font-medium`} aria-current="true">{content}</button>
                : <Link href={href} className={row}>{content}</Link>}
            </PopoverClose>
          );
        })}
      </HeaderPopover>
    </Popover>
  );
}
