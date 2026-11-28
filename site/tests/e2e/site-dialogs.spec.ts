import { expect, test } from '@playwright/test';
import { observeLayoutShifts, readLayoutShifts } from './layout-shifts';

test('the shared search modal opens centered with a dim backdrop', async ({ page }) => {
  for (const [path, colorScheme, alpha] of [
    ['/', 'light', 0.4], ['/docs', 'dark', 0.5],
  ] as const) {
    await page.emulateMedia({ colorScheme });
    await page.goto(path);
    const opening = page.locator('[data-search-full]:visible, [data-search]:visible');
    await opening.click();
    const dialog = page.getByRole('dialog', { name: 'Search', exact: true });
    await expect(dialog).toHaveCSS('opacity', '1');
    const measured = await dialog.evaluate((node) => {
      const { x, y, width, height } = node.getBoundingClientRect();
      const backdrop = getComputedStyle(node.previousElementSibling!);
      return {
        offsetX: Math.abs(x + width / 2 - window.innerWidth / 2),
        offsetY: Math.abs(y + height / 2 - window.innerHeight / 2),
        width,
        height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        backdrop: backdrop.backgroundColor,
        backdropOpacity: Number(backdrop.opacity),
      };
    });
    expect(measured.offsetX).toBeLessThanOrEqual(2);
    expect(measured.offsetY).toBeLessThanOrEqual(measured.viewportHeight * .1);
    expect(measured.height).toBeLessThanOrEqual(measured.viewportHeight * .85 + 1);
    expect(measured.width).toBe(measured.viewportWidth < 560 ? measured.viewportWidth - 32 : 600);
    expect(measured.backdrop).toBe(`rgba(0, 0, 0, ${alpha})`);
    expect(measured.backdropOpacity).toBeGreaterThan(.9);
    await expect(dialog.getByRole('combobox')).toBeFocused();
    const scroll = await page.evaluate(() => window.scrollY);
    await page.mouse.move(4, 4);
    await page.mouse.wheel(0, 300);
    await expect(page.locator('body')).toHaveAttribute('data-scroll-locked');
    expect(await page.evaluate(() => window.scrollY)).toBe(scroll);
    if (path === '/') await page.mouse.click(4, 4);
    else await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
});

for (const [platform, token, label, shortcutKey] of [
  ['Linux', 'X11; Linux x86_64', 'Ctrl', 'Control+k'],
  ['macOS', 'Macintosh; Intel Mac OS X 10_15_7', '⌘', 'Meta+k'],
] as const) {
  test.describe(`${platform} search shortcut`, () => {
    test.use({ userAgent: `Mozilla/5.0 (${token}) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36` });

    test('keeps its first-paint label and natural keycap geometry through hydration', async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.addInitScript(observeLayoutShifts);
      let hydrate!: () => void;
      const beforeHydration = new Promise<void>((resolve) => { hydrate = resolve; });
      await page.route('**/_next/static/chunks/*.js', async (route) => {
        await beforeHydration;
        await route.continue();
      });

      const keycaps = page.locator('[data-search-full] kbd');
      const geometry = () => keycaps.evaluateAll((nodes) => nodes.map((node) => {
        const { x, width } = node.getBoundingClientRect();
        const text = [...node.children].find((child) => child.getClientRects().length) ?? node;
        return { x, width, textX: text.getBoundingClientRect().left };
      }));
      try {
        await page.goto('/', { waitUntil: 'commit' });
        await expect(keycaps.first().locator('span:visible')).toHaveText(label);
        await expect(keycaps.first().locator('span')).toHaveText(['⌘', 'Ctrl']);
        await page.evaluate(() => document.fonts.ready);
        const before = await geometry();
        expect(before).toHaveLength(2);
        if (platform === 'macOS') expect(before[0].width).toBeLessThanOrEqual(before[1].width + 2);
        hydrate();
        await page.waitForLoadState('load');
        await expect(keycaps.first().locator('span:visible')).toHaveText(label);
        expect(await geometry()).toEqual(before);
        const shifts = await page.evaluate(readLayoutShifts);
        expect(shifts.reduce((total, shift) => total + shift.value, 0), JSON.stringify(shifts)).toBe(0);
        await page.keyboard.press(shortcutKey);
        await expect(page.getByRole('dialog', { name: 'Search', exact: true })).toBeVisible();
      } finally {
        hydrate();
      }
    });
  });
}
