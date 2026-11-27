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
    return [
      { source: '/docs/about', destination: '/about', permanent: true },
      { source: `/:lang(${i18n.languages.join('|')})/docs/about`, destination: '/:lang/about', permanent: true },
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
