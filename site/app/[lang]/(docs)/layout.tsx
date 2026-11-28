import { notFound } from 'next/navigation';
import { DocsShell } from '@/components/docs-shell';
import { DocsSiteHeader } from '@/components/docs-version-control';
import { isLocale } from '@/lib/i18n';
import { docsLoaders, repository } from '@/lib/source';

export default async function Layout({ params, children }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();

  const available = [...docsLoaders].filter(([, source]) => source.getPage([], lang));
  const trees = Object.fromEntries(available.map(([version, source]) => [version, source.getPageTree(lang)]));
  const versions = available.map(([version, source]) => ({
    version,
    pages: Object.fromEntries(source.getPages(lang).map((page) => [page.slugs.join('/'), page.url])),
  }));
  return (
    <>
      <DocsSiteHeader lang={lang} repository={repository} versions={versions} />
      <DocsShell lang={lang} askAI={process.env.NEXT_PUBLIC_ASK_AI === '1'} trees={trees}>
        {children}
      </DocsShell>
    </>
  );
}
