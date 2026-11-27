import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { Brand } from '@/components/brand';
import { CopyInstall, LandingLanguage, LandingTheme } from '@/components/landing-controls';
import { isLocale, localize } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';
import { pageMetadata } from '@/lib/metadata';
import { Conversation } from '@/components/conversation';
import { StepFlow } from '@/components/step-flow';
import { SwitchboardHero } from '@/components/switchboard-hero';
import { repository } from '@/lib/source';

export async function generateMetadata({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const { brand, intro } = landingCopy[lang];
  return pageMetadata(lang, '/', brand, intro);
}

export default async function Landing({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const copy = landingCopy[lang];
  const docs = localize(lang, '/docs');
  const home = localize(lang, '/');

  return (
    <div className="min-h-dvh">
      <header>
        <nav className="mx-auto flex min-h-16 max-w-6xl flex-wrap items-center justify-between gap-x-3 px-5 py-2.5 sm:px-8">
          <Link href={home}><Brand name={copy.brand} /></Link>
          <div className="flex items-center gap-2.5 text-sm sm:gap-5">
            <Link className="swb-text-link" href={docs}>{copy.docs}</Link>
            <a className="swb-text-link" href={repository}>{copy.github}</a>
            <LandingLanguage lang={lang} />
            <LandingTheme lang={lang} />
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-5 sm:px-8">
        <section className="flex flex-col items-center py-14 text-center sm:py-20 lg:py-24">
          <h1 className="text-display-xl swb-hero-title">{copy.headline}</h1>
          <div className="mt-8"><CopyInstall lang={lang} /></div>
          <div className="mt-5 flex flex-wrap justify-center gap-2.5">
            <Link className="swb-button bg-fd-primary text-fd-primary-foreground hover:bg-fd-primary/85" href={docs}>
              {copy.docs}<ArrowRight aria-hidden="true" className="size-4" />
            </Link>
            <a className="swb-button border hover:bg-fd-muted" href={repository}>{copy.github}</a>
          </div>
        </section>
        <SwitchboardHero label={copy.boardAlt} />
        <Conversation title={copy.convoTitle} channel={copy.channel} you={copy.you} lines={copy.convo} />
        <StepFlow steps={copy.steps} />
      </main>
      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm text-fd-muted-foreground sm:px-8">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <Link className="swb-text-link" href={docs}>{copy.docs}</Link>
            <a className="swb-text-link" href={repository}>{copy.github}</a>
            <Link className="swb-text-link" href={localize(lang, '/about')}>{copy.about}</Link>
            <a className="swb-text-link" href="https://www.apache.org/licenses/LICENSE-2.0">{copy.license}</a>
          </div>
          <LandingLanguage lang={lang} />
        </div>
      </footer>
    </div>
  );
}
