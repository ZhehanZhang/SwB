import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import type { NextConfig } from 'next';
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from 'next/constants';
import { createMDX } from 'fumadocs-mdx/next';
import { parse } from 'jsonc-parser';
import { i18n } from './lib/i18n';
import { promptSourceFiles, readPromptFile } from './lib/prompt-source';

const { vars } = parse(readFileSync(new URL('./wrangler.jsonc', import.meta.url), 'utf8'));
const locales = i18n.languages.filter((lang) => lang !== i18n.defaultLanguage).join('|');
const markdown = (prefix: string, targetPrefix = prefix) => [
  { source: `${prefix}/docs.mdx`, destination: `/llms.mdx${targetPrefix}/docs` },
  { source: `${prefix}/docs/:slug+.mdx`, destination: `/llms.mdx${targetPrefix}/docs/:slug+` },
  { source: `${prefix}/about.mdx`, destination: `/llms.mdx${targetPrefix}/about` },
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
        ...markdown('/en', ''),
        ...markdown(locale),
        { source: '/en/llms-full.txt', destination: '/llms-full.txt' },
        { source: '/', destination: '/en' },
        { source: '/docs/:slug*', destination: '/en/docs/:slug*' },
        { source: '/about', destination: '/en/about' },
      ],
    };
  },
  async redirects() {
    const localized = `/:lang(${i18n.languages.join('|')})`;
    const nonEnglish = `/:lang(${locales})`;
    return [
      { source: '/docs/about', destination: '/about', permanent: true },
      { source: `${localized}/docs/about`, destination: '/:lang/about', permanent: true },
      { source: '/docs/trademarks', destination: '/about', permanent: true },
      { source: `${localized}/docs/trademarks`, destination: '/:lang/about', permanent: true },
      { source: '/docs/trademarks.mdx', destination: '/about.mdx', permanent: true },
      { source: `${localized}/docs/trademarks.mdx`, destination: '/:lang/about.mdx', permanent: true },
      { source: '/llms.mdx/docs/trademarks', destination: '/llms.mdx/about', permanent: true },
      { source: `/llms.mdx${nonEnglish}/docs/trademarks`, destination: '/llms.mdx/:lang/about', permanent: true },
      ...[
        ['/docs/self-hosting', '/docs/deploy'],
        ['/docs/setup/cloudflare', '/docs/cloudflare'],
      ].flatMap(([from, to]) => [
        { source: from, destination: to, permanent: true },
        { source: `${localized}${from}`, destination: `/:lang${to}`, permanent: true },
        ...['.md', '.mdx'].flatMap((extension) => [
          { source: `${from}${extension}`, destination: `${to}.mdx`, permanent: true },
          { source: `${localized}${from}${extension}`, destination: `/:lang${to}.mdx`, permanent: true },
        ]),
        { source: `/llms.mdx${from}`, destination: `/llms.mdx${to}`, permanent: true },
        { source: `/llms.mdx/en${from}`, destination: `/llms.mdx${to}`, permanent: true },
        { source: `/llms.mdx${nonEnglish}${from}`, destination: `/llms.mdx/:lang${to}`, permanent: true },
      ]),
    ];
  },
};

export default function nextConfig(phase: string) {
  if (phase === PHASE_DEVELOPMENT_SERVER || phase === PHASE_PRODUCTION_BUILD) {
    const directory = new URL('./.generated/', import.meta.url);
    const target = new URL('prompts.json', directory);
    const bundle = JSON.stringify(Object.fromEntries(promptSourceFiles().map((file) => [file, readPromptFile(file)])));
    if (!existsSync(target) || readFileSync(target, 'utf8') !== bundle) {
      mkdirSync(directory, { recursive: true });
      writeFileSync(target, bundle);
    }
  }
  return createMDX()(config);
}
