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
