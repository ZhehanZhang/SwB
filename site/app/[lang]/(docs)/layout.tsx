import { notFound } from 'next/navigation';
import { Brand } from '@/components/brand';
import { DocsShell } from '@/components/docs-shell';
import { isLocale, localize } from '@/lib/i18n';
import { repository, source } from '@/lib/source';

export default async function Layout({ params, children }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();

  return (
    <DocsShell lang={lang} askAI={process.env.NEXT_PUBLIC_ASK_AI === '1'} tree={source.getPageTree(lang)}
      nav={{ title: <Brand name="SwB" />, url: localize(lang, '/') }}
      githubUrl={repository}>
      {children}
    </DocsShell>
  );
}
