import { defineI18n } from 'fumadocs-core/i18n';

export const locales = ['en', 'zh-Hans', 'zh-Hant', 'es', 'fr', 'de', 'ko', 'ja'] as const;
export type Locale = (typeof locales)[number];

export const localeNames = {
  en: 'English',
  'zh-Hans': '简体中文',
  'zh-Hant': '繁體中文',
  es: 'Español',
  fr: 'Français',
  de: 'Deutsch',
  ko: '한국어',
  ja: '日本語',
} satisfies Record<Locale, string>;

export const localeCookie = 'swb-locale';

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

export function isDocsPath(path: string) {
  const [root, first, second] = path.split('/', 3);
  return root === '' && (first === 'docs' || (second === 'docs' && isLocale(first)));
}

export function localize(lang: Locale, path: string) {
  return lang === 'en' ? path : `/${lang}${path === '/' ? '' : path}`;
}
