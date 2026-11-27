import { defineI18n } from 'fumadocs-core/i18n';

export const locales = ['en', 'zh-Hans', 'zh-Hant', 'es', 'fr', 'de', 'ko', 'ja'] as const;
export type Locale = (typeof locales)[number];

export const i18n = defineI18n({
  defaultLanguage: 'en',
  languages: [...locales],
  hideLocale: 'default-locale',
  parser: 'dir',
  fallbackLanguage: null,
});

export function isLocale(lang: string): lang is Locale {
  return locales.some((locale) => locale === lang);
}

export function localize(lang: Locale, path: string) {
  return lang === 'en' ? path : `/${lang}${path === '/' ? '' : path}`;
}
