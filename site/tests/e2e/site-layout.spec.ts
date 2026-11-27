import { expect, test, type Page } from '@playwright/test';

async function headerGeometry(page: Page) {
  return page.locator('#nd-nav').evaluate((header) => {
    const rect = (item: Element) => {
      const { x, y, width, height } = item.getBoundingClientRect();
      return { x, y, width, height };
    };
    return Object.fromEntries([
      ['header', rect(header)] as const,
      ...Array.from(header.querySelectorAll<HTMLElement>('[data-header-item]'))
        .filter((item) => item.getClientRects().length)
        .map((item) => [item.dataset.headerItem!, rect(item)] as const),
    ]);
  });
}

test('header and all items stay within 0.5px across home and docs', async ({ page }) => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    const home = await headerGeometry(page);
    const tabs = width === 390 ? [] : ['docs', 'setup', 'about'];
    const rest = width === 390 ? ['menu'] : [];
    expect(Object.keys(home)).toEqual([
      'header', 'brand', ...tabs, width < 1024 ? 'search-icon' : 'search', 'theme', 'language', 'github', ...rest,
    ]);

    for (const path of ['/docs', '/docs/install']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const docs = await headerGeometry(page);
      expect(Object.keys(docs)).toEqual(Object.keys(home));
      for (const item of Object.keys(home)) {
        for (const edge of ['x', 'y', 'width', 'height'] as const) {
          expect(Math.abs(docs[item][edge] - home[item][edge]), `${width}px ${path} ${item} ${edge}`).toBeLessThanOrEqual(0.5);
        }
      }
    }
  }
});

test('Ask AI clears the last paragraph, footer and cards on short and long docs pages', async ({ page }) => {
  for (const path of ['/docs', '/docs/install']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight - window.scrollY))
      .toBeLessThanOrEqual(1);
    const ask = page.getByRole('button', { name: 'Ask AI' });
    const footer = page.locator('[data-docs-footer]');
    const lastParagraph = page.locator('.swb-mdx > p').last();
    await expect(ask).toBeVisible();
    await expect(lastParagraph).toBeVisible();
    await expect(footer.locator('a').first()).toBeInViewport();
    const button = (await ask.boundingBox())!;
    const reserved = await page.locator('.swb-docs-with-chat').evaluate((node) => parseFloat(getComputedStyle(node).paddingBlockEnd));
    const bottomOffset = page.viewportSize()!.height - (button.y + button.height);
    expect(reserved).toBeGreaterThanOrEqual(button.height + bottomOffset);
    const content = [footer, lastParagraph, ...await footer.locator('a').all()];
    for (const item of content) {
      const rect = (await item.boundingBox())!;
      const intersects = button.x < rect.x + rect.width && button.x + button.width > rect.x
        && button.y < rect.y + rect.height && button.y + button.height > rect.y;
      expect(intersects, `${path} ${await item.innerText()}`).toBe(false);
    }
  }
});

test('the open-source statement is visible in all landing heroes', async ({ page }) => {
  for (const [path, text] of [
    ['/', 'Free and open source under Apache-2.0'],
    ['/zh-Hans', '免费开源，采用 Apache-2.0 许可'],
    ['/zh-Hant', '免費開源，採用 Apache-2.0 授權'],
  ]) {
    await page.goto(path);
    const license = page.locator('.swb-hero-copy').getByRole('link', { name: text });
    await expect(license).toBeInViewport();
    await expect(license).toHaveAttribute('href', 'https://github.com/ZhehanZhang/SwB/blob/main/LICENSE');
  }
});
