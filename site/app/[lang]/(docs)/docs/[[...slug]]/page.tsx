import { notFound } from 'next/navigation';
import { Document } from '@/components/document';
import { docsLoaders, docsPage, latestPageHref } from '@/lib/source';
import { latestDocsVersion } from '@/lib/docs-version';
import { isLocale } from '@/lib/i18n';
import { pageMetadata } from '@/lib/metadata';

type Props = PageProps<'/[lang]/docs/[[...slug]]'>;

export const dynamicParams = false;

async function getPage(params: Props['params']) {
  const { lang, slug } = await params;
  if (!isLocale(lang)) notFound();
  const doc = docsPage(slug, lang);
  if (!doc) notFound();
  return { lang, ...doc };
}

export function generateStaticParams() {
  return [...docsLoaders].flatMap(([version, versionLoader]) =>
    version === latestDocsVersion
      ? versionLoader.generateParams().filter(({ slug }) => slug.join('/') !== 'about')
      : versionLoader.generateParams().map(({ lang, slug }) => ({ lang, slug: [`v${version}`, ...slug] })),
  );
}

export async function generateMetadata({ params }: Props) {
  const { lang, page, version, versionLoader } = await getPage(params);
  return pageMetadata(lang, page.url, page.data.title, page.data.description, {
    url: page.url,
    hrefFor: (locale) => versionLoader.getPage(page.slugs, locale)?.url,
    defaultHref: version === latestDocsVersion ? undefined : latestPageHref(page.slugs),
    markdown: version === latestDocsVersion,
  });
}

export default async function Page({ params }: Props) {
  const { page, version } = await getPage(params);
  return <Document page={page} version={version} />;
}
