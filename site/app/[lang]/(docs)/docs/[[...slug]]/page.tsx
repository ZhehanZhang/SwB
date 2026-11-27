import { notFound } from 'next/navigation';
import { Document } from '@/components/document';
import { isLocale } from '@/lib/i18n';
import { pageMetadata } from '@/lib/metadata';
import { source } from '@/lib/source';

type Props = PageProps<'/[lang]/docs/[[...slug]]'>;

async function getPage(params: Props['params']) {
  const { lang, slug } = await params;
  if (!isLocale(lang)) notFound();
  const page = source.getPage(slug, lang);
  if (!page || page.slugs.join('/') === 'about') notFound();
  return { lang, page };
}

export function generateStaticParams() {
  return source.generateParams().filter(({ slug }) => slug.join('/') !== 'about');
}

export async function generateMetadata({ params }: Props) {
  const { lang, page } = await getPage(params);
  return pageMetadata(lang, page.url, page.data.title, page.data.description, page);
}

export default async function Page({ params }: Props) {
  const { page } = await getPage(params);
  return <Document page={page} />;
}
