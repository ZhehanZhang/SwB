import type { Metadata } from 'next';
import { locales, localize, type Locale } from './i18n';
import { markdownUrl, siteUrl, source, type DocsPage } from './source';

export function languageAlternates(path: string, page?: DocsPage) {
  const alternates: Record<string, string> = {};
  for (const lang of locales) {
    const href = page ? source.getPage(page.slugs, lang)?.url : localize(lang, path);
    if (href) alternates[lang] = new URL(href, siteUrl).href;
  }
  if (alternates.en) alternates['x-default'] = alternates.en;
  return alternates;
}

export function pageMetadata(
  lang: Locale,
  path: string,
  title: string,
  description?: string,
  page?: DocsPage,
): Metadata {
  const url = page?.url ?? localize(lang, path);

  return {
    title,
    description,
    alternates: {
      canonical: url,
      languages: languageAlternates(path, page),
      types: page ? { 'text/markdown': markdownUrl(page) } : undefined,
    },
    openGraph: {
      title,
      description,
      type: 'website',
      siteName: 'SwB',
      url,
      locale: { en: 'en_US', 'zh-Hans': 'zh_CN', 'zh-Hant': 'zh_TW', es: 'es_ES', fr: 'fr_FR', de: 'de_DE', ko: 'ko_KR', ja: 'ja_JP' }[lang],
      images: [{ url: { en: '/og/og.png', 'zh-Hans': '/og/og-zh-hans.png', 'zh-Hant': '/og/og-zh-hant.png', es: '/og/og.png', fr: '/og/og.png', de: '/og/og.png', ko: '/og/og.png', ja: '/og/og.png' }[lang], alt: 'SwB', width: 1200, height: 630 }],
    },
  };
}
