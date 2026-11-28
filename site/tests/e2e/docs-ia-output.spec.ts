import { expect, test } from '@playwright/test';
import { locales, localize } from '../../lib/i18n';

const site = 'https://swb.one';
const bookmarks = {
  '/docs/deploy': ['run-on-your-computer', 'docker-compose', 'cloudflare-tunnel', 'one-click-deploy', 'fly', 'render', 'railway'],
  '/docs/cloudflare': ['quick-tunnel', 'named-tunnel', 'trusted-proxies', 'cloudflare-access'],
  '/docs/operate': ['backups-and-upgrades', 'export-memory'],
};
const movedPaths = Object.keys(bookmarks);
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
  test(`docs headings expose stable bookmarks in ${locale}`, async ({ page }) => {
    for (const [path, ids] of Object.entries(bookmarks)) {
      const url = localize(locale, path);
      await page.goto(url);
      for (const id of ids) {
        await expect(page.locator(`[id="${id}"]`), `${url}#${id}`).toHaveRole('heading');
      }
    }
  });

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

test('the previous Cloudflare console bookmarks still resolve', async ({ page }) => {
  await page.goto('/docs/cloudflare#protect-the-console');
  await expect(page.locator('span[id="protect-the-console"]')).toHaveCount(1);
  await page.goto('/es/docs/cloudflare#proteger-la-consola');
  await expect(page.locator('span[id="proteger-la-consola"]')).toHaveCount(1);
});
