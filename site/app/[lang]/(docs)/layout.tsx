import { notFound } from 'next/navigation';
import { DocsShell } from '@/components/docs-shell';
import { isLocale } from '@/lib/i18n';
import { source } from '@/lib/source';

export default async function Layout({ params, children }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();

  return (
    <DocsShell lang={lang} askAI={process.env.NEXT_PUBLIC_ASK_AI === '1'} tree={source.getPageTree(lang)}>
      {children}
    </DocsShell>
  );
}
