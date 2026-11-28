import { notFound } from 'next/navigation';
import { SiteHeader } from '@/components/site-header';
import { isLocale } from '@/lib/i18n';
import { repository } from '@/lib/urls';

export default async function Layout({ params, children }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return <><SiteHeader lang={lang} repository={repository} />{children}</>;
}
