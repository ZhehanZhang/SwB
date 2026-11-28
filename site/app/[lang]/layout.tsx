import type { Metadata, Viewport } from 'next';
import { notFound } from 'next/navigation';
import { i18nProvider } from 'fumadocs-ui/i18n';
import { Providers } from '@/components/providers';
import { isLocale, locales } from '@/lib/i18n';
import { siteUrl } from '@/lib/urls';
import { translations } from '@/lib/translations';
import { interTight, jetbrainsMono, swbInter } from '@/lib/fonts';
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
    <html lang={lang} className={`${swbInter.variable} ${interTight.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <head>
        <meta name="darkreader-lock" />
      </head>
      <body>
        <Providers i18n={i18nProvider(translations, lang)}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
