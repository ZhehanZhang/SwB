import { expect, test } from '@playwright/test';
import { locales, localize } from '../../lib/i18n';

const site = 'https://swb.one';
const movedPaths = ['/docs/deploy', '/docs/cloudflare', '/docs/operate'];
const retiredPath = /\/docs\/(?:self-hosting|setup\/cloudflare)(?=[#?)\s<]|\.mdx?\b|$)/;

test('the sitemap and llms index advertise the new routes in every locale', async ({ request }) => {
  const [sitemap, index] = await Promise.all(['/sitemap.xml', '/llms.txt'].map((path) => request.get(path)));
  expect(sitemap.ok()).toBe(true);
  expect(index.ok()).toBe(true);
  const [xml, text] = await Promise.all([sitemap.text(), index.text()]);
  for (const locale of locales) {
    for (const path of movedPaths) {
      const url = `${site}${localize(locale, path)}`;
      expect(xml).toContain(`<loc>${url}</loc>`);
      expect(text).toContain(`](${url})`);
    }
  }
  expect(xml).not.toMatch(retiredPath);
  expect(text).not.toMatch(retiredPath);
});

for (const locale of locales) {
  test(`Markdown and llms-full serve the new routes in ${locale}`, async ({ request }) => {
    const response = await request.get(localize(locale, '/llms-full.txt'));
    expect(response.ok()).toBe(true);
    const full = await response.text();
    expect(full).not.toMatch(retiredPath);
    for (const path of movedPaths) {
      const url = localize(locale, path);
      const markdown = await request.get(`${url}.mdx`, { maxRedirects: 0 });
      expect(markdown.status(), url).toBe(200);
      expect(markdown.headers()['content-type'], url).toContain('text/markdown');
      const text = await markdown.text();
      expect(text).toContain(`\nURL: ${site}${url}\n`);
      expect(full).toContain(text);
    }
  });
}
