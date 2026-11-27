import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { Brand } from '@/components/brand';
import { GitHubMark } from '@/components/github-mark';
import { localize, type Locale } from './i18n';
import { repository } from './source';

const docsLabel: Record<Locale, string> = { en: 'Docs', 'zh-Hans': '文档', 'zh-Hant': '文件' };

export function baseOptions(lang: Locale): BaseLayoutProps {
  return {
    nav: { title: <Brand name="SwB" />, url: localize(lang, '/') },
    links: [
      { text: docsLabel[lang], url: localize(lang, '/docs'), active: 'nested-url' },
      { type: 'icon', text: 'GitHub', label: 'GitHub', icon: <GitHubMark />, url: repository, external: true },
    ],
  };
}
