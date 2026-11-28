import { DocsBody, DocsPage, DocsTitle, PageFooter, ViewOptionsPopover } from 'fumadocs-ui/layouts/notebook/page';
import { ThemeSwitch } from 'fumadocs-ui/layouts/shared/slots/theme-switch';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { latestDocsVersion } from '@/lib/docs-version';
import { docsLoaders, markdownUrl, repository, siteUrl, type DocsPage as DocumentPage } from '@/lib/source';
import { releaseBranch } from '@/lib/urls';
import { MdxLink } from '@/lib/mdx-address';
import type { Locale } from '@/lib/i18n';
import { useMDXComponents } from '@/mdx-components';
import footerStyles from './docs-footer.module.css';
import { DocsNavigation } from './docs-navigation';
import styles from './page-actions.module.css';
import { PersonalizedMarkdownCopy } from './personalized-docs';
import { TranslationNote } from './translation-note';

export function Document({ page, version = latestDocsVersion }: { page: DocumentPage; version?: string }) {
  const { body: MDX, title, toc, full } = page.data;
  const hasToc = Boolean(toc?.length);
  const versionLoader = docsLoaders.get(version)!;
  const latest = version === latestDocsVersion;
  const markdown = latest ? markdownUrl(page) : undefined;
  const sourcePath = latest ? page.path : `versions/${version}/${page.path}`;
  const githubUrl = `${repository}/blob/${releaseBranch}/docs/${sourcePath.split('/').map(encodeURIComponent).join('/')}`;
  const startNext = page.slugs.join('/') === 'get-started' ? versionLoader.getPage([], page.locale) : undefined;

  return (
    <DocsPage toc={toc} full={full} data-swb-toc={hasToc || undefined}
      breadcrumb={{ className: 'swb-docs-breadcrumb' }} tableOfContent={{ enabled: hasToc }}
      tableOfContentPopover={{ enabled: hasToc }}
      footer={{ component: (
        <footer className={footerStyles.footer} data-docs-footer>
          <PageFooter className={footerStyles.pagination} data-docs-pagination items={startNext ? {
            next: { name: startNext.data.title, description: startNext.data.description, url: startNext.url },
          } : undefined} />
          <div className={footerStyles.controls}><ThemeSwitch className="swb-theme-toggle" /></div>
        </footer>
      ) }}>
      <DocsTitle className="font-display font-extrabold tracking-tight">{title}</DocsTitle>
      <div className={styles.row} data-docs-actions>
        {markdown && <PersonalizedMarkdownCopy lang={page.locale as Locale} markdownUrl={markdown} />}
        <ViewOptionsPopover className={styles.button} markdownUrl={markdown} githubUrl={githubUrl} pageUrl={new URL(markdown ?? page.url, siteUrl).href} />
        <DocsNavigation askAI={process.env.NEXT_PUBLIC_ASK_AI === '1'} />
      </div>
      <TranslationNote lang={page.locale ?? 'en'} url={page.url} />
      <DocsBody className="swb-mdx">
        <MDX components={useMDXComponents({
          a: createRelativeLink(versionLoader, { ...page, path: page.path.split('/').slice(1).join('/') }, MdxLink),
        })} />
      </DocsBody>
    </DocsPage>
  );
}
