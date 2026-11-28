import { expect, test, type Page } from '@playwright/test';
import { latestDocsVersion } from '../../lib/docs-version';

const fixture = '0.9.0';
const archive = `/docs/v${fixture}`;
const fixtureEnabled = process.env.SWB_TEST_DOCS_VERSION === '1';
const trigger = (page: Page) => page.locator('[data-header-item="version"]');
const versions = (page: Page) => page.getByRole('dialog', { name: 'Docs version' });

test('latest docs show a compact version control with a working current entry', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() => localStorage.setItem('swb-address', 'https://swb.example.net'));
  await page.goto('/docs/install?source=versions');
  const button = trigger(page);
  const header = page.locator('#nd-nav');
  await expect(button).toHaveAccessibleName(`Docs version ${latestDocsVersion}`);
  await expect(header.locator('[data-header-item="address"]')).toBeVisible();
  const before = await header.boundingBox();
  const dimension = await button.evaluate((node) => {
    const { left, right, height } = node.getBoundingClientRect();
    const address = document.querySelector('[data-header-item="address"]')!;
    const neighbor = address.getBoundingClientRect();
    const text = node.querySelector('span > span')!.getBoundingClientRect();
    const icon = node.querySelector('svg')!.getBoundingClientRect();
    const glyphOffset = Math.abs(text.top + text.height / 2 - icon.top - icon.height / 2);
    return { left, right, height, addressHeight: neighbor.height, gap: left - neighbor.right, glyphOffset, viewport: window.innerWidth };
  });
  expect(dimension.left).toBeGreaterThanOrEqual(20);
  expect(dimension.right).toBeLessThanOrEqual(dimension.viewport - 20);
  expect(dimension.height).toBe(dimension.addressHeight);
  expect(dimension.gap).toBeGreaterThan(0);
  expect(dimension.gap).toBeLessThanOrEqual(8);
  expect(dimension.glyphOffset).toBeLessThanOrEqual(1);
  await button.click();
  const panel = versions(page);
  await expect(panel).toBeVisible();
  expect((await panel.boundingBox())!.width).toBeLessThanOrEqual(160);
  const current = panel.getByRole('button', { name: `${latestDocsVersion} latest` });
  await expect(current).toHaveAttribute('aria-current', 'true');
  if (!fixtureEnabled) await expect(panel.getByRole('link', { name: fixture, exact: true })).toHaveCount(0);
  await current.click();
  await expect(panel).toBeHidden();
  await expect(page).toHaveURL('/docs/install?source=versions');
  expect(await header.boundingBox()).toEqual(before);
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(panel).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(button).toBeFocused();
});

test('latest build excludes the fixture and keeps its Markdown and LLM routes', async ({ request }) => {
  test.skip(fixtureEnabled, 'Run against a normal build');
  expect((await request.get(archive)).status()).toBe(404);
  expect((await request.get('/api/search', { params: { query: 'SwB', version: fixture } })).status()).toBe(404);
  expect(await (await request.get('/sitemap.xml')).text()).not.toContain(archive);
  expect(await (await request.get('/llms.txt')).text()).toContain('https://swb.one/docs/install');
  const response = await request.get('/docs/install.mdx');
  expect(response.ok()).toBe(true);
  expect(response.headers()['content-type']).toContain('text/markdown');
});

test.describe('archived docs fixture', () => {
  test.skip(!fixtureEnabled, 'Set SWB_TEST_DOCS_VERSION=1');

  test('prerenders localized routes with self canonicals and a latest hreflang default', async ({ page, request }) => {
    const languages = { en: '', 'zh-Hans': '/zh-Hans', 'zh-Hant': '/zh-Hant' };
    for (const [lang, prefix] of Object.entries(languages)) {
      expect((await page.goto(`${prefix}${archive}/install`))?.status()).toBe(200);
      await expect(page.locator('html')).toHaveAttribute('lang', lang);
      await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', `https://swb.one${prefix}${archive}/install`);
      await expect(page.locator('link[rel="alternate"][hreflang]')).toHaveCount(4);
      for (const [translation, path] of Object.entries({ ...languages, 'x-default': '' })) {
        await expect(page.locator(`link[rel="alternate"][hreflang="${translation}"]`))
          .toHaveAttribute('href', `https://swb.one${path}${translation === 'x-default' ? '/docs' : archive}/install`);
      }
      await expect(page.locator('link[type="text/markdown"]')).toHaveCount(0);
    }
    const negotiated = await request.get(archive, { maxRedirects: 0, headers: { 'Accept-Language': 'zh-TW', 'User-Agent': 'Mozilla/5.0' } });
    expect(negotiated.status()).toBe(307);
    expect(new URL(negotiated.headers().location, negotiated.url()).pathname).toBe(`/zh-Hant${archive}`);
    for (const path of [`${archive}/missing`, `/docs/v${latestDocsVersion}`, `${archive}/install.mdx`, '/docs/v8.6.4']) {
      expect((await request.get(path)).status(), path).toBe(404);
    }
    expect(await (await request.get('/llms.txt')).text()).not.toContain(archive);
  });

  test('switches to the same page or the target index and keeps snapshot links scoped', async ({ page }) => {
    await page.goto('/docs/install');
    await trigger(page).click();
    await versions(page).getByRole('link', { name: fixture, exact: true }).click();
    await expect(page).toHaveURL(`${archive}/install`);
    await expect(page.getByRole('heading', { name: 'Archived install' })).toBeVisible();
    await expect(trigger(page)).toHaveText(fixture);
    await trigger(page).click();
    await versions(page).getByRole('link', { name: `${latestDocsVersion} latest` }).click();
    await expect(page).toHaveURL('/docs/install');

    await page.goto('/docs/setup/claude');
    await trigger(page).click();
    await versions(page).getByRole('link', { name: fixture, exact: true }).click();
    await expect(page).toHaveURL(archive);
    const article = page.locator('.swb-mdx');
    await expect(article.getByRole('link', { name: 'install page' })).toHaveAttribute('href', `${archive}/install#start-swb`);
    await article.getByRole('link', { name: 'Archive records' }).click();
    await expect(page).toHaveURL(`${archive}/archived-only`);
    await trigger(page).click();
    await versions(page).getByRole('link', { name: `${latestDocsVersion} latest` }).click();
    await expect(page).toHaveURL('/docs');

    await page.goto('/zh-Hant/docs/install');
    await trigger(page).click();
    await page.getByRole('dialog', { name: '文件版本' }).getByRole('link', { name: fixture, exact: true }).click();
    await expect(page).toHaveURL(`/zh-Hant${archive}/install`);
  });

  test('search and Ask AI use the viewed version while search defaults to latest', async ({ page, request }) => {
    const query = 'amber rhomboid';
    const latest = await request.get('/api/search', { params: { query } });
    const snapshot = await request.get('/api/search', { params: { query, version: fixture } });
    expect(latest.ok()).toBe(true);
    expect(snapshot.ok()).toBe(true);
    expect(await latest.json()).toEqual([]);
    expect(await snapshot.json()).toContainEqual(expect.objectContaining({ type: 'page', url: `${archive}/install` }));

    await page.goto(`${archive}/install`);
    const searchDialog = page.getByRole('dialog', { name: 'Search' });
    await page.locator('[data-search-full]:visible, [data-search]:visible').click();
    await searchDialog.getByRole('combobox').fill(query);
    await expect(page.getByRole('option', { name: /Archived install/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(searchDialog).toBeHidden();
    for (const shortcut of ['Control+k', 'Meta+k']) {
      await page.keyboard.press(shortcut);
      await expect(searchDialog.getByRole('combobox')).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(searchDialog).toBeHidden();
    }

    await page.route('**/api/chat', (route) => route.fulfill({ status: 503, body: 'Test response' }));
    for (const [path, version] of [[`${archive}/install`, fixture], ['/docs/install', latestDocsVersion]]) {
      await page.goto(path);
      await page.getByRole('button', { name: 'Ask AI', exact: true }).click();
      const question = page.getByRole('textbox', { name: 'Message' });
      await question.fill(query);
      const outbound = page.waitForRequest((candidate) => candidate.url().endsWith('/api/chat') && candidate.method() === 'POST');
      await question.press('Enter');
      expect((await outbound).postDataJSON()).toMatchObject({
        lang: 'en', version, messages: [{ role: 'user', text: query, location: { location: path } }],
      });
    }
  });
});
