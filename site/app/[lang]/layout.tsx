import type { Metadata, Viewport } from 'next';
import { notFound } from 'next/navigation';
import { i18nProvider } from 'fumadocs-ui/i18n';
import { Providers } from '@/components/providers';
import { SiteHeader } from '@/components/site-header';
import { isLocale, locales } from '@/lib/i18n';
import { repository, siteUrl } from '@/lib/source';
import { translations } from '@/lib/translations';
import { inter, interTight, jetbrainsMono } from '@/lib/fonts';
import '../global.css';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  icons: {
    icon: [{ url: '/favicon.ico', sizes: 'any' }, { url: '/swb.svg', type: 'image/svg+xml' }],
    apple: '/apple-touch-icon.png',
  },
};

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#09090b' },
  ],
};

export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export default async function LocaleLayout({ children, params }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();

  return (
    <html lang={lang} className={`${inter.variable} ${interTight.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <head>
        <meta name="darkreader-lock" />
      </head>
      <body>
        <Providers i18n={i18nProvider(translations, lang)}>
          <SiteHeader lang={lang} repository={repository} />
          {children}
        </Providers>
      </body>
    </html>
  );
}
