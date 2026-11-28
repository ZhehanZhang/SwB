import { expect, test } from '@playwright/test';

for (const prefix of ['', '/de', '/es', '/fr', '/ja', '/ko', '/zh-Hans', '/zh-Hant']) {
  test(`Memory scope tokens fit on one line in ${prefix.slice(1) || 'en'}`, async ({ page }) => {
    await page.goto(`${prefix}/docs/memory`);
    await page.evaluate(() => document.fonts.ready);
    const table = page.getByRole('table').first();
    const tokens = table.locator('td code');
    await expect(tokens).toHaveCount(6);
    const layout = await table.evaluate((element) => {
      const scroller = element.parentElement!;
      scroller.scrollLeft = scroller.scrollWidth;
      return {
        wrapped: [...element.querySelectorAll('td code')].filter((code) => code.getClientRects().length !== 1)
          .map((code) => code.textContent),
        proseWidth: element.querySelector('tbody tr td:last-child')!.getBoundingClientRect().width,
        pageOverflow: document.documentElement.scrollWidth - innerWidth,
        overflow: scroller.scrollWidth - scroller.clientWidth,
        scrolled: scroller.scrollLeft,
      };
    });
    expect(layout.wrapped).toEqual([]);
    expect(layout.proseWidth).toBeGreaterThanOrEqual(160);
    expect(layout.pageOverflow).toBeLessThanOrEqual(1);
    if (layout.overflow > 1) expect(layout.scrolled).toBeCloseTo(layout.overflow, 0);
    if (!prefix && page.viewportSize()!.width === 390) expect(layout.overflow).toBeGreaterThan(1);
  });
}
