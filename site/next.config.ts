import { readFileSync } from 'node:fs';
import type { NextConfig } from 'next';
import { createMDX } from 'fumadocs-mdx/next';
import { parse } from 'jsonc-parser';
import { i18n } from './lib/i18n';

const { vars } = parse(readFileSync(new URL('./wrangler.jsonc', import.meta.url), 'utf8'));
const locales = i18n.languages.filter((lang) => lang !== i18n.defaultLanguage).join('|');
const markdown = (prefix: string) => [
  { source: `${prefix}/docs.mdx`, destination: `/llms.mdx${prefix}/docs` },
  { source: `${prefix}/docs/:slug+.mdx`, destination: `/llms.mdx${prefix}/docs/:slug+` },
  { source: `${prefix}/about.mdx`, destination: `/llms.mdx${prefix}/about` },
];

const config: NextConfig = {
  turbopack: {
    resolveAlias: { 'lucide-react': './node_modules/lucide-react' },
  },
  env: {
    NEXT_PUBLIC_ASK_AI: process.env.NEXT_PUBLIC_ASK_AI ?? vars.NEXT_PUBLIC_ASK_AI,
  },
  async rewrites() {
    const locale = `/:lang(${locales})`;
    return {
      beforeFiles: [
        ...markdown(''),
        ...markdown(locale),
        { source: '/', destination: '/en' },
        { source: '/docs/:slug*', destination: '/en/docs/:slug*' },
        { source: '/about', destination: '/en/about' },
      ],
    };
  },
  async redirects() {
    return [
      { source: '/en', destination: '/', permanent: true },
      { source: '/en/docs/:slug*', destination: '/docs/:slug*', permanent: true },
      { source: '/en/about', destination: '/about', permanent: true },
      { source: '/en/llms-full.txt', destination: '/llms-full.txt', permanent: true },
      { source: '/docs/about', destination: '/about', permanent: true },
      { source: `/:lang(${locales})/docs/about`, destination: '/:lang/about', permanent: true },
    ];
  },
};

export default createMDX()(config);
