import { parseArgs } from 'node:util';
import { locales, localize } from '../lib/i18n.ts';
import { siteUrl } from '../lib/urls.ts';

const { values } = parseArgs({ options: { 'base-url': { type: 'string', default: siteUrl } } });
const baseUrl = URL.parse(values['base-url']);
if (!baseUrl || !['http:', 'https:'].includes(baseUrl.protocol) || baseUrl.username || baseUrl.password) {
  console.error('Deploy smoke check requires an HTTP(S) origin in --base-url.');
  process.exit(1);
}

const deepDocTitles = {
  en: 'MCP client',
  'zh-Hans': 'MCP 客户端',
  'zh-Hant': 'MCP 用戶端',
  es: 'Cliente MCP',
  fr: 'Client MCP',
  de: 'MCP-Client',
  ko: 'MCP 클라이언트',
  ja: 'MCP クライアント',
};

const checks = [
  ...locales.flatMap((lang) => {
    const title = deepDocTitles[lang];
    if (!title) throw new Error(`Deploy smoke check needs a deep docs title for ${lang}.`);
    return [
      { path: localize(lang, '/docs'), lang, title: 'SwB', marker: /<h1\b[^>]*>SwB<\/h1>/ },
      { path: localize(lang, '/docs/setup/mcp-client'), lang, title },
    ];
  }),
  { path: '/', lang: 'en', title: 'SwB', marker: /<h1\b[^>]*\sid="landing-heading"/ },
  { path: '/about', lang: 'en', title: 'About' },
  { path: '/prompts/claude.md', contentType: 'text/markdown', marker: /^Help me connect Claude to SwB\./m },
  { path: '/llms.txt', contentType: 'text/plain', marker: `${siteUrl}/prompts/claude.md)` },
];

const results = await Promise.allSettled(checks.map(async ({ path, lang, title, marker, contentType = 'text/html' }) => {
  const response = await fetch(new URL(path, baseUrl), {
    redirect: 'follow',
    headers: { 'accept-language': 'en' },
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}${response.redirected ? ' after redirect' : ''}`);
  const receivedType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
  if (receivedType !== contentType) throw new Error(`expected content type ${contentType}, got ${receivedType ?? 'none'}`);

  const body = await response.text();
  if (lang && body.match(/<html\b[^>]*\slang="([^"]+)"/)?.[1] !== lang) {
    throw new Error(`missing HTML language ${lang}`);
  }
  if (title && !body.includes(`<title>${title}</title>`)) throw new Error(`missing title "${title}"`);
  if (marker && !(typeof marker === 'string' ? body.includes(marker) : marker.test(body))) {
    throw new Error(`missing body marker ${marker}`);
  }
}));

const failures = results.flatMap((result, index) =>
  result.status === 'rejected' ? [`  ${checks[index].path}: ${result.reason.message}`] : [],
);
if (failures.length) {
  console.error(`Deploy smoke check failed at ${baseUrl.origin}:\n${failures.join('\n')}`);
  process.exitCode = 1;
} else {
  console.log(`Deploy smoke check passed for ${checks.length} routes at ${baseUrl.origin}.`);
}
