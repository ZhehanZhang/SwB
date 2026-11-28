import { expect, test } from '@playwright/test';
import { seedHero } from './hero-seed';

for (const [path, inlineWidth] of [['/', 480], ['/zh-Hans', 452], ['/de', 500]] as const) {
  test(`${path} keeps two hero arrangements and fully opaque settled pills from 320px to 1600px`, async ({ page }) => {
    test.setTimeout(60_000);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await seedHero(page);
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const subtitle = page.locator('[data-hero-pair]');
    const active = subtitle.locator('.swb-chip-on');
    const chips = subtitle.locator('.swb-chip');
    const arrangements = new Set<string>();
    await expect(active.first()).toHaveCSS('transition-duration', '0.18s');

    for (let width = 320; width <= 1600; width += 20) {
      await page.setViewportSize({ width, height: 900 });
      const at = `${path} at ${width}px`;
      await expect.poll(() => chips.evaluateAll((items) => items.every((item) => {
        const opacity = Number(getComputedStyle(item).opacity);
        return item.classList.contains('swb-chip-on') ? opacity === 1 : opacity === 0;
      })), { message: at, timeout: 1400 }).toBe(true);
      const { copy, actions, license, command, prefix, slots, conjunction } = await subtitle.evaluate((line) => {
        const box = (node: Element) => node.getBoundingClientRect().toJSON();
        const copy = line.closest('.swb-hero-copy')!;
        const find = (selector: string) => copy.querySelector(selector)!;
        const prefix = line.querySelector(':scope > .swb-between-text');
        return {
          copy: box(copy), actions: box(find('[data-hero-actions]')), license: box(find('[data-hero-license]')),
          command: box(find('[data-install-chip]')), prefix: prefix ? box(prefix) : null,
          slots: [...line.querySelectorAll('.swb-slot')].map(box),
          conjunction: box(line.querySelector('.swb-pair-lead .swb-between-text')!),
        };
      });
      if (copy.width >= inlineWidth) {
        arrangements.add('inline');
        expect(command.left, at).toBeGreaterThan(actions.right);
        expect(Math.abs(command.top + command.bottom - actions.top - actions.bottom), at).toBeLessThanOrEqual(1);
        expect(license.top, at).toBeGreaterThanOrEqual(Math.max(actions.bottom, command.bottom));
      } else {
        arrangements.add('stacked');
        expect(license.top, at).toBeGreaterThan(actions.bottom);
        expect(command.top, at).toBeGreaterThan(license.bottom);
        expect(Math.abs(command.left - actions.left), at).toBeLessThanOrEqual(1);
      }
      expect(Math.max(actions.right, license.right, command.right), at).toBeLessThanOrEqual(copy.right + 1);
      if (path === '/zh-Hans') expect(prefix, at).toBeNull();
      else {
        expect(prefix, at).not.toBeNull();
        expect(Math.min(...slots.map((slot) => slot.top)), at).toBeGreaterThanOrEqual(prefix!.bottom);
      }
      expect(Math.abs(conjunction.left - slots[0].right - 8), at).toBeLessThanOrEqual(1);
      expect(Math.max(...slots.map((slot) => slot.right)), at).toBeLessThanOrEqual(copy.right + 1);
    }
    expect([...arrangements].sort()).toEqual(['inline', 'stacked']);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(active.first()).toHaveCSS('transition-duration', '0s');
  });
}
