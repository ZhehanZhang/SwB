import { notFound } from 'next/navigation';
import { Document } from '@/components/document';
import { isLocale } from '@/lib/i18n';
import { pageMetadata } from '@/lib/metadata';
import { source } from '@/lib/source';

type Props = PageProps<'/[lang]/about'>;

export const dynamicParams = false;

async function getAbout(params: Props['params']) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const page = source.getPage(['about'], lang);
  if (!page) notFound();
  return { lang, page };
}

export async function generateMetadata({ params }: Props) {
  const { lang, page } = await getAbout(params);
  return pageMetadata(lang, '/about', page.data.title, page.data.description, {
    url: page.url,
    hrefFor: (locale) => source.getPage(page.slugs, locale)?.url,
  });
}

export default async function About({ params }: Props) {
  const { page } = await getAbout(params);
  return <Document page={page} />;
}
