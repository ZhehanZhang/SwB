import { DocsBody, DocsPage, DocsTitle, MarkdownCopyButton, ViewOptionsPopover } from 'fumadocs-ui/layouts/docs/page';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { markdownUrl, repository, siteUrl, source, type DocsPage as DocumentPage } from '@/lib/source';
import { useMDXComponents } from '@/mdx-components';

export function Document({ page }: { page: DocumentPage }) {
  const { body: MDX, title, toc, full } = page.data;
  const markdown = markdownUrl(page);
  const githubUrl = `${repository}/blob/main/site/content/docs/${page.path.split('/').map(encodeURIComponent).join('/')}`;

  return (
    <DocsPage toc={toc} full={full} tableOfContent={{ enabled: Boolean(toc?.length) }}
      tableOfContentPopover={{ enabled: Boolean(toc?.length) }}>
      <DocsTitle className="font-display font-extrabold tracking-tight">{title}</DocsTitle>
      <div className="mt-2 flex flex-wrap items-center gap-2 border-b pb-5">
        <MarkdownCopyButton markdownUrl={markdown} />
        <ViewOptionsPopover markdownUrl={markdown} githubUrl={githubUrl} pageUrl={new URL(markdown, siteUrl).href} />
      </div>
      <DocsBody className="swb-mdx">
        <MDX components={useMDXComponents({
          a: createRelativeLink(source, { ...page, path: page.path.split('/').slice(1).join('/') }),
        })} />
      </DocsBody>
    </DocsPage>
  );
}
