import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { i18nProvider } from 'fumadocs-ui/i18n';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { isLocale, locales } from '@/lib/i18n';
import { siteUrl } from '@/lib/source';
import { translations } from '@/lib/translations';
import '../global.css';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  icons: {
    icon: [{ url: '/favicon.ico', sizes: 'any' }, { url: '/swb.svg', type: 'image/svg+xml' }],
    apple: '/apple-touch-icon.png',
  },
};

export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export default async function LocaleLayout({ children, params }: LayoutProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();

  return (
    <html lang={lang} suppressHydrationWarning>
      <body>
        <RootProvider i18n={i18nProvider(translations, lang)}>{children}</RootProvider>
      </body>
    </html>
  );
}
