import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { Brand } from '@/components/brand';
import { GitHubMark } from '@/components/github-mark';
import { localize, type Locale } from './i18n';
import { setupPickerCopy } from './setup-picker-copy';
import { repository } from './source';

const navLabels: Record<Locale, { docs: string; about: string }> = {
  en: { docs: 'Docs', about: 'About' },
  'zh-Hans': { docs: '文档', about: '关于' },
  'zh-Hant': { docs: '文件', about: '關於' },
};

export function baseOptions(lang: Locale): BaseLayoutProps {
  const labels = navLabels[lang];
  return {
    nav: { title: <Brand name="SwB" />, url: localize(lang, '/') },
    links: [
      { text: labels.docs, url: localize(lang, '/docs'), active: 'url' },
      { text: setupPickerCopy[lang].setup, url: localize(lang, '/docs/setup'), active: 'nested-url' },
      { text: labels.about, url: localize(lang, '/about') },
      { type: 'icon', text: 'GitHub', label: 'GitHub', icon: <GitHubMark />, url: repository, external: true },
    ],
  };
}
