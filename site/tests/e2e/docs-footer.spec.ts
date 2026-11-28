import { expect, test } from '@playwright/test';

for (const [path, cardCount, edge] of [
  ['/docs/get-started', 1, 'next'], ['/docs/install', 2, 'both'], ['/docs/about', 1, 'previous'],
] as const) {
  test(`${path} footer fits its cards and marks the end of document scroll`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(path);
    await expect(page.locator('#nd-sidebar')).toHaveCount(page.viewportSize()!.width < 768 ? 0 : 1);
    await page.evaluate(() => document.fonts.ready);
    const footer = page.locator('[data-docs-footer]');
    const cards = footer.locator('[data-docs-pagination] > a');
    await expect(cards).toHaveCount(cardCount);
    await expect(footer).toHaveCSS('border-top-width', '1px');
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => footer.evaluate((node) => Math.max(
      Math.abs(document.documentElement.scrollHeight - window.innerHeight - window.scrollY),
      Math.abs(window.innerHeight - node.getBoundingClientRect().bottom - 24),
    ))).toBeLessThanOrEqual(1);
    const foot = (await footer.boundingBox())!;
    const body = (await page.locator('#nd-page .swb-mdx').boundingBox())!;
    const theme = (await footer.getByRole('button', { name: 'Toggle Theme' }).boundingBox())!;
    const measured = await Promise.all((await cards.all()).map((card) => card.boundingBox()));
    expect(foot.y - (body.y + body.height)).toBeGreaterThanOrEqual(32);
    expect(Math.abs(foot.x + foot.width - (theme.x + theme.width))).toBeLessThanOrEqual(1);
    for (const [index, card] of measured.entries()) {
      expect(Math.abs(card!.width - (foot.width - 12) / 2)).toBeLessThanOrEqual(1);
      const target = index === 1 || edge === 'next' ? foot.x + foot.width - card!.width : foot.x;
      expect(Math.abs(card!.x - target)).toBeLessThanOrEqual(1);
      expect(card!.y - foot.y).toBeGreaterThanOrEqual(24);
      expect(theme.y - (card!.y + card!.height)).toBeGreaterThanOrEqual(16);
    }
    if (cardCount === 2) expect(measured[0]!.y).toBe(measured[1]!.y);
  });
}

test('a short docs page accounts for the header in its minimum height', async ({ page }) => {
  await page.setViewportSize({ width: page.viewportSize()!.width, height: 1800 });
  await page.goto('/docs/get-started');
  await page.evaluate(() => document.fonts.ready);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight)).toBe(1800);
  await expect(page.locator('[data-docs-footer] [data-theme-toggle]')).toBeInViewport();
});

test('the sidebar scrolls independently to its last link at the end of a docs page', async ({ page }) => {
  const { width } = page.viewportSize()!;
  const mobile = width < 768;
  await page.setViewportSize({ width, height: 600 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/docs/get-started');
  await expect(page.locator('#nd-sidebar')).toHaveCount(mobile ? 0 : 1);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(page.locator('[data-docs-footer] [data-theme-toggle]')).toBeInViewport();
  if (mobile) {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole('button', { name: 'Open Sidebar', exact: true }).click();
  }
  const sidebar = page.locator(mobile ? '#nd-sidebar-mobile' : '#nd-sidebar');
  const viewport = sidebar.locator('[data-radix-scroll-area-viewport]');
  const setup = sidebar.getByRole('button', { name: 'Setup', exact: true });
  if (await setup.getAttribute('data-state') === 'closed') await setup.click();
  const last = viewport.getByRole('link').last();
  await expect(last).toHaveText('About');
  await viewport.evaluate((node) => Promise.allSettled(node.getAnimations({ subtree: true }).map(({ finished }) => finished)));
  await expect.poll(() => viewport.evaluate((node) => node.scrollHeight - node.clientHeight)).toBeGreaterThan(0);
  const documentScroll = await page.evaluate(() => window.scrollY);
  await viewport.hover();
  await page.mouse.wheel(0, 3000);
  await expect(last).toBeInViewport({ ratio: 1 });
  await last.click({ trial: true });
  expect(await page.evaluate(() => window.scrollY)).toBe(documentScroll);
  if (!mobile) {
    const bounds = (await viewport.boundingBox())!;
    expect(Math.abs(bounds.y - 56)).toBeLessThanOrEqual(1);
    expect(Math.abs(bounds.y + bounds.height - 600)).toBeLessThanOrEqual(1);
  }
});
