import { expect, test, type APIResponse } from '@playwright/test';
import { locales } from '../../lib/i18n';

const site = 'https://swb.one';
const noRedirects = { maxRedirects: 0 };
const chinesePreference = { 'Accept-Language': 'zh-TW', Cookie: 'swb-locale=zh-Hans' };

test.use({ extraHTTPHeaders: { 'User-Agent': 'Mozilla/5.0' } });

function location(response: APIResponse) {
  const target = response.headers().location;
  return target ? new URL(target, response.url()).href : undefined;
}

function expectNegotiated(response: APIResponse) {
  const headers = response.headers();
  if (response.status() === 307) {
    expect(headers.vary?.toLowerCase().split(/,\s*/)).toEqual(
      expect.arrayContaining(['accept-language', 'cookie', 'user-agent']),
    );
  }
  expect(headers['cache-control']).toContain('no-store');
}

for (const [acceptLanguage, destination] of [
  ['zh-TW', '/zh-Hant'],
  ['zh-HK', '/zh-Hant'],
  ['zh-MO', '/zh-Hant'],
  ['ZH-hant-CN', '/zh-Hant'],
  ['zh', '/zh-Hans'],
  ['zh-CN', '/zh-Hans'],
  ['zh-SG', '/zh-Hans'],
  ['zh-Hans-HK', '/zh-Hans'],
  ['en-US;q=0.4, zh-CN;q=0.8', '/zh-Hans'],
  ['zh-TW;q=0, zh-CN;q=0.5', '/zh-Hans'],
  ['zh-TW;q=1.5, zh-CN;q=0.5', '/zh-Hans'],
  ['fr-CA, zh-HK;q=0.8', '/fr'],
  ['zh-TW;q=0.8, zh-CN;q=0.8', '/zh-Hant'],
  ['zh-TW-X-demo;q=0.9, en;q=0.7', '/zh-Hant'],
  ['zh-CN-x-tw', '/zh-Hans'],
  ['zh-CN;q=0.8, en-GB', undefined],
  ['zh-CN;q=0, en;q=0', undefined],
  ['zh-HK;q=invalid, fr-FR', '/fr'],
  ['zho, fr-FR, *', '/fr'],
  ['es-419', '/es'],
  ['de-CH;q=0.9, en;q=0.8', '/de'],
  ['ko-KR', '/ko'],
  ['ja-JP, en;q=0.5', '/ja'],
  ['pt-BR, it', undefined],
  [undefined, undefined],
] as const) {
  test(`negotiates ${acceptLanguage ?? 'an absent language header'}`, async ({ request }) => {
    const response = await request.get('/', {
      ...noRedirects,
      headers: acceptLanguage === undefined ? {} : { 'Accept-Language': acceptLanguage },
    });
    expect(response.status()).toBe(destination ? 307 : 200);
    expect(location(response)).toBe(destination ? new URL(destination, response.url()).href : undefined);
    expectNegotiated(response);
  });
}

test('saved locale takes precedence and redirects keep the path and query', async ({ request }) => {
  const response = await request.get('/docs/install?ref=setup&name=swb', {
    ...noRedirects, headers: chinesePreference,
  });
  expect(response.status()).toBe(307);
  expect(location(response)).toBe(new URL('/zh-Hans/docs/install?ref=setup&name=swb', response.url()).href);
  expectNegotiated(response);

  const invalidCookie = await request.get('/about', {
    ...noRedirects, headers: { ...chinesePreference, Cookie: 'swb-locale=zh-TW' },
  });
  expect(invalidCookie.status()).toBe(307);
  expect(location(invalidCookie)).toBe(new URL('/zh-Hant/about', invalidCookie.url()).href);

  const english = await request.get('/docs', {
    ...noRedirects, headers: { ...chinesePreference, Cookie: 'swb-locale=en' },
  });
  expect(english.status()).toBe(200);
  expect(location(english)).toBeUndefined();
  expectNegotiated(english);
});

for (const userAgent of ['Googlebot/2.1', 'ExampleCrawler', 'ExampleSpider', 'Python-urllib/3.13', 'curl/8.7.1']) {
  test(`serves English to ${userAgent} despite Chinese preferences`, async ({ request }) => {
    const response = await request.get('/docs', {
      ...noRedirects, headers: { ...chinesePreference, 'User-Agent': userAgent },
    });
    expect(response.status()).toBe(200);
    expect(location(response)).toBeUndefined();
    expect((await response.text()).match(/<html lang="([^"]+)"/)?.[1]).toBe('en');
    expectNegotiated(response);
  });
}

for (const [path, contentType] of [
  ['/docs.mdx', 'text/markdown'],
  ['/docs/install.mdx', 'text/markdown'],
  ['/about.mdx', 'text/markdown'],
  ['/en/docs.mdx', 'text/markdown'],
  ['/en/docs/install.mdx', 'text/markdown'],
  ['/en/about.mdx', 'text/markdown'],
  ['/zh-Hant/docs/install.mdx', 'text/markdown'],
  ['/llms.mdx/docs/install', 'text/markdown'],
  ['/llms.txt', 'text/plain'],
  ['/llms-full.txt', 'text/plain'],
  ['/en/llms-full.txt', 'text/plain'],
  ['/zh-Hant/llms-full.txt', 'text/plain'],
  ['/prompts', 'text/plain'],
  ['/prompts/claude.md', 'text/markdown'],
  ['/api/search?query=swb', 'application/json'],
  ['/brand/swb.svg', 'image/svg+xml'],
  ['/swb.svg', 'image/svg+xml'],
] as const) {
  test(`bypasses locale selection on ${path}`, async ({ request }) => {
    const response = await request.get(path, { ...noRedirects, headers: chinesePreference });
    expect(response.status()).toBe(200);
    expect(location(response)).toBeUndefined();
    expect(response.headers()['content-type']).toContain(contentType);
    expect(response.headers().vary?.toLowerCase() ?? '').not.toContain('accept-language');
  });
}

test('does not redirect a missing prompt under Chinese preferences', async ({ request }) => {
  const response = await request.get('/prompts/x.md', { ...noRedirects, headers: chinesePreference });
  expect(response.status()).toBe(404);
  expect(location(response)).toBeUndefined();
});

for (const [path, destination] of [
  ['/docs/about', '/about'],
  ['/en/docs/about', '/en/about'],
  ['/zh-Hant/docs/about', '/zh-Hant/about'],
] as const) {
  test(`preserves the permanent alias from ${path}`, async ({ request }) => {
    const response = await request.get(path, { ...noRedirects, headers: chinesePreference });
    expect(response.status()).toBe(308);
    expect(location(response)).toBe(new URL(destination, response.url()).href);
  });
}

test('permanently redirects the retired policy in all locales and formats', async ({ request }) => {
  const retired = '/docs/trademarks';
  for (const prefix of ['', ...locales.map((lang) => `/${lang}`)]) {
    const routes = [
      [`${prefix}${retired}`, `${prefix}/about`],
      [`${prefix}${retired}.mdx`, `${prefix}/about.mdx`],
    ];
    if (prefix !== '/en') routes.push([`/llms.mdx${prefix}${retired}`, `/llms.mdx${prefix}/about`]);
    for (const [path, destination] of routes) {
      const response = await request.get(`${path}?ref=old`, { ...noRedirects, headers: chinesePreference });
      expect(response.status(), path).toBe(308);
      expect(location(response), path).toBe(new URL(`${destination}?ref=old`, response.url()).href);
    }
  }
});

for (const path of ['/', '/docs', '/docs/install', '/about']) {
  const suffix = path === '/' ? '' : path;
  const alternates: Record<string, string> = {
    en: `${site}${path}`,
    'zh-Hans': `${site}/zh-Hans${suffix}`,
    'zh-Hant': `${site}/zh-Hant${suffix}`,
    ...Object.fromEntries(['es', 'fr', 'de', 'ko', 'ja'].map((lang) => [lang, `${site}/${lang}${suffix}`])),
    'x-default': `${site}${path}`,
  };

  for (const [lang, route] of [
    ['en', path],
    ['en', `/en${suffix}`],
    ['zh-Hans', `/zh-Hans${suffix}`],
    ['zh-Hant', `/zh-Hant${suffix}`],
    ['ja', `/ja${suffix}`],
  ] as const) {
    test(`serves ${route} with canonical and hreflang URLs`, async ({ request }) => {
      const headers = route === path || lang !== 'en'
        ? { ...chinesePreference, Cookie: 'swb-locale=en' }
        : chinesePreference;
      const response = await request.get(route, { ...noRedirects, headers });
      expect(response.status()).toBe(200);
      expect(location(response)).toBeUndefined();

      const html = await response.text();
      expect(html.match(/<html lang="([^"]+)"/)?.[1]).toBe(lang);
      const links = html.match(/<link\b[^>]*>/g) ?? [];
      const href = (link: string) => {
        const value = link.match(/\bhref="([^"]+)"/)?.[1];
        return value && new URL(value).href;
      };
      const canonical = links.filter((link) => link.includes('rel="canonical"')).map(href);
      const hreflang = links.flatMap((link) => {
        const language = link.match(/\bhref[lL]ang="([^"]+)"/)?.[1];
        return language && link.includes('rel="alternate"') ? [[language, href(link)]] : [];
      });
      expect(canonical).toEqual([alternates[lang]]);
      expect(Object.fromEntries(hreflang)).toEqual(alternates);
    });
  }
}
