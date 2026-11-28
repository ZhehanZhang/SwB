import { localize, type Locale } from './i18n';
import { setupPickerCopy } from './setup-picker-copy';

export const navLabels: Record<Locale, { docs: string; about: string; menu: string }> = {
  en: { docs: 'Docs', about: 'About', menu: 'Toggle Menu' },
  'zh-Hans': { docs: '文档', about: '关于', menu: '菜单' },
  'zh-Hant': { docs: '文件', about: '關於', menu: '選單' },
  es: {
    "docs": "Docs",
    "about": "Acerca de",
    "menu": "Menú"
  },
  fr: {
    "docs": "Docs",
    "about": "À propos",
    "menu": "Menu"
  },
  de: {
    "docs": "Doku",
    "about": "Über SwB",
    "menu": "Menü"
  },
  ko: {
    "docs": "문서",
    "about": "소개",
    "menu": "메뉴"
  },
  ja: {
    "docs": "ドキュメント",
    "about": "SwB について",
    "menu": "メニューを開閉"
  },
};

export function headerLinks(lang: Locale) {
  const labels = navLabels[lang];
  return [
    { id: 'docs', text: labels.docs, href: localize(lang, '/docs') },
    { id: 'setup', text: setupPickerCopy[lang].setup, href: localize(lang, '/docs/setup') },
    { id: 'about', text: labels.about, href: localize(lang, '/about') },
  ] as const;
}
