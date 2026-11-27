import { DocsBody, DocsPage, DocsTitle, MarkdownCopyButton, PageFooter, ViewOptionsPopover } from 'fumadocs-ui/layouts/notebook/page';
import { ThemeSwitch } from 'fumadocs-ui/layouts/shared/slots/theme-switch';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { markdownUrl, repository, siteUrl, source, type DocsPage as DocumentPage } from '@/lib/source';
import { releaseBranch } from '@/lib/urls';
import { useMDXComponents } from '@/mdx-components';
import { DocsNavigation } from './docs-navigation';

export function Document({ page }: { page: DocumentPage }) {
  const { body: MDX, title, toc, full } = page.data;
  const markdown = markdownUrl(page);
  const githubUrl = `${repository}/blob/${releaseBranch}/docs/${page.path.split('/').map(encodeURIComponent).join('/')}`;
  const startNext = page.slugs.join('/') === 'get-started' ? source.getPage([], page.locale) : undefined;

  return (
    <DocsPage toc={toc} full={full} breadcrumb={{ className: 'swb-docs-breadcrumb' }} tableOfContent={{ enabled: Boolean(toc?.length) }}
      tableOfContentPopover={{ enabled: Boolean(toc?.length) }}
      footer={{ component: (
        <footer data-docs-footer>
          <PageFooter items={startNext ? {
            next: { name: startNext.data.title, description: startNext.data.description, url: startNext.url },
          } : undefined} />
          <div className="mt-4 flex justify-end"><ThemeSwitch className="swb-theme-toggle" /></div>
        </footer>
      ) }}>
      <DocsTitle className="font-display font-extrabold tracking-tight">{title}</DocsTitle>
      <div className="mt-2 flex flex-wrap items-center gap-2 border-b pb-5">
        <MarkdownCopyButton className="swb-button swb-button-compact swb-doc-copy" markdownUrl={markdown} />
        <ViewOptionsPopover className="swb-button swb-button-compact flex-row-reverse" markdownUrl={markdown} githubUrl={githubUrl} pageUrl={new URL(markdown, siteUrl).href} />
        <DocsNavigation askAI={process.env.NEXT_PUBLIC_ASK_AI === '1'} />
      </div>
      <DocsBody className="swb-mdx">
        <MDX components={useMDXComponents({
          a: createRelativeLink(source, { ...page, path: page.path.split('/').slice(1).join('/') }),
        })} />
      </DocsBody>
    </DocsPage>
  );
}
