import { expect, test, type Locator } from '@playwright/test';
import { locales, localize } from '../../lib/i18n';

const compact = [[360, 2], [390, 2], [768, 2]];
const wide = [[1024, 4], [1440, 4], [1920, 4]];

async function measure(picker: Locator) {
  return picker.evaluate((grid) => {
    const bounds = grid.getBoundingClientRect();
    const tiles = Array.from(grid.children).filter((tile) => tile.matches('button, label, a'));
    const tileBounds = tiles.map((tile) => tile.getBoundingClientRect());
    return {
      columns: new Set(tileBounds.map(({ left }) => left)).size,
      rows: new Set(tileBounds.map(({ top }) => top)).size,
      gap: getComputedStyle(grid).gap,
      tiles: tiles.map((tile, index) => {
        const box = tileBounds[index];
        const name = tile.querySelector('[data-app-picker-name]')!;
        const mark = tile.querySelector('.swb-mark-tile')!.getBoundingClientRect();
        const check = tile.querySelector(':scope > svg')?.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(name);
        const lines = Array.from(range.getClientRects());
        return {
          name: name.textContent,
          height: box.height,
          lineCount: lines.length,
          fits: box.left >= bounds.left && box.right <= Math.min(bounds.right, window.innerWidth) + 1
            && (!check || check.right < box.right)
            && lines.every(({ left, right }) => left >= mark.right && right < (check?.left ?? box.right)),
        };
      }),
    };
  });
}

for (const locale of locales) {
  test(`${locale} app pickers use the same density and keep each name on one line`, async ({ page, context }) => {
    const sizes = page.viewportSize()!.width < 768 ? compact : wide;
    const setup = await context.newPage();
    await Promise.all([page, setup].map(async (tab) => {
      await tab.emulateMedia({ reducedMotion: 'reduce' });
      await tab.setViewportSize({ width: sizes[0][0], height: 900 });
    }));
    await Promise.all([
      page.goto(localize(locale, '/docs/get-started')),
      setup.goto(localize(locale, '/docs/setup')),
    ]);
    await expect(page.locator('[data-get-started]')).toHaveAttribute('data-ready', 'true');
    await expect(setup.locator('html')).toHaveAttribute('lang', locale);
    const toggles = page.locator('[data-app-picker="toggle"]');
    const checkboxes = setup.locator('[data-app-picker="checkbox"]');
    await expect(toggles).toBeVisible();
    await expect(checkboxes).toBeVisible();
    await Promise.all([page, setup].map((tab) => tab.evaluate(() => document.fonts.ready.then(() => null))));

    for (const [width, columns] of sizes) {
      await test.step(`${width}px has ${columns} columns`, async () => {
        await Promise.all([page, setup].map((tab) => tab.setViewportSize({ width, height: 900 })));
        const [start, guides] = await Promise.all([measure(toggles), measure(checkboxes)]);
        expect(start).toEqual(guides);
        expect(start.columns).toBe(columns);
        expect(start.rows).toBe(Math.ceil(16 / columns));
        expect(start.gap).toBe('8px');
        expect(start.tiles).toHaveLength(16);
        for (const { name, height, lineCount, fits } of start.tiles) {
          expect(height, name ?? '').toBe(44);
          expect(lineCount, name ?? '').toBe(1);
          expect(fits, name ?? '').toBe(true);
        }
      });
    }
  });
}

test('keyboard selection keeps each picker’s own behavior', async ({ page }) => {
  await page.goto('/docs/get-started');
  await expect(page.locator('[data-get-started]')).toHaveAttribute('data-ready', 'true');
  const toggle = page.locator('[data-app-picker="toggle"]').getByRole('button', { name: 'Claude', exact: true });
  await toggle.press('Space');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page).toHaveURL(/agents=claude&run=local$/);
  await expect(page.getByRole('region', { name: 'Claude', exact: true })).toBeVisible();

  await page.goto('/docs/setup');
  const picker = page.locator('[data-setup-picker]');
  const checkbox = picker.getByRole('checkbox', { name: 'Claude', exact: true });
  const preview = picker.getByRole('textbox', { name: 'Setup prompt' });
  await checkbox.press('Space');
  await expect(checkbox).toBeChecked();
  await expect(preview).toHaveValue(/### Claude/);
  await expect(page).toHaveURL('/docs/setup');
  await checkbox.press('Space');
  await expect(checkbox).not.toBeChecked();
  await expect(preview).toHaveValue('');
});
