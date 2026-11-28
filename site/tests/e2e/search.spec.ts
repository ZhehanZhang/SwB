import { expect, test } from '@playwright/test';
import { locales, localize } from '../../lib/i18n';

test('search serves the built docs in each locale', async ({ request }) => {
  for (const locale of locales) {
    const response = await request.get('/api/search', { params: { query: 'MCP', locale } });
    expect(response.status(), locale).toBe(200);
    const results: { type: string; url: string }[] = await response.json();
    expect(results.some(({ type, url }) => type === 'page' && url.startsWith(localize(locale, '/docs/'))), locale).toBe(true);
    expect(results.every(({ url }) => url.startsWith(localize(locale, '/docs')) || url.startsWith(localize(locale, '/about'))), locale).toBe(true);
  }
});

test('search generates results for the current Deploy and Cloudflare URLs', async ({ request }) => {
  for (const locale of locales) {
    for (const [query, page] of [['Railway', '/docs/deploy'], ['Cloudflare', '/docs/cloudflare']]) {
      const response = await request.get('/api/search', { params: { query, locale } });
      expect(response.status(), `${locale} ${query}`).toBe(200);
      const results: { type: string; url: string }[] = await response.json();
      expect(results, `${locale} ${query}`).toContainEqual(expect.objectContaining({ type: 'page', url: localize(locale, page) }));
      expect(results.map(({ url }) => url).join('\n')).not.toMatch(/\/docs\/(?:self-hosting|setup\/cloudflare)(?:#|$)/m);
    }
  }
});
