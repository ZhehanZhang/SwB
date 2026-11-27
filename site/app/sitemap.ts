import type { MetadataRoute } from 'next';
import { locales, localize } from '@/lib/i18n';
import { languageAlternates } from '@/lib/metadata';
import { siteUrl, source } from '@/lib/source';

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    ...locales.map((lang) => ({
      url: new URL(localize(lang, '/'), siteUrl).href,
      alternates: { languages: languageAlternates('/') },
    })),
    ...source.getPages().map((page) => ({
      url: new URL(page.url, siteUrl).href,
      alternates: { languages: languageAlternates(page.url, page) },
    })),
  ];
}
