import { expect, test } from '@playwright/test';
import { seedHero } from './hero-seed';

for (const [path, inlineWidth] of [['/', 480], ['/zh-Hans', 452], ['/de', 500], ['/ja', 536]] as const) {
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

    const widths = Array.from({ length: 65 }, (_, index) => 320 + 20 * index);
    widths.splice(widths.indexOf(440), 0, 430);
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const at = `${path} at ${width}px`;
      await expect.poll(() => chips.evaluateAll((items) => items.every((item) => {
        const opacity = Number(getComputedStyle(item).opacity);
        return item.classList.contains('swb-chip-on') ? opacity === 1 : opacity === 0;
      })), { message: at, timeout: 1400 }).toBe(true);
      const { copy, actions, license, command, prefix, slots, conjunction, suffix, suffixRects, longestPair } = await subtitle.evaluate((line) => {
        const box = (node: Element) => node.getBoundingClientRect().toJSON();
        const copy = line.closest('.swb-hero-copy')!;
        const find = (selector: string) => copy.querySelector(selector)!;
        const prefix = line.querySelector(':scope > .swb-between-text');
        const suffix = line.querySelector('.swb-pair-suffix');
        const suffixRange = document.createRange();
        if (suffix) suffixRange.selectNodeContents(suffix);
        const [lead, tail] = [...line.querySelectorAll('.swb-slot')];
        const widest = [...lead.children].map((chip) => parseFloat(getComputedStyle(chip).width)).sort((a, b) => b - a);
        const slotStyle = getComputedStyle(lead);
        const border = parseFloat(slotStyle.borderLeftWidth) + parseFloat(slotStyle.borderRightWidth);
        const conjunction = box(line.querySelector('.swb-pair-lead .swb-between-text')!);
        return {
          copy: box(copy), actions: box(find('[data-hero-actions]')), license: box(find('[data-hero-license]')),
          command: box(find('[data-install-chip]')), prefix: prefix ? box(prefix) : null,
          slots: [box(lead), box(tail)], conjunction,
          suffix: suffix ? box(suffix) : null, suffixRects: suffix ? suffixRange.getClientRects().length : 0,
          longestPair: widest[0] + widest[1] + 2 * border + conjunction.width + 16,
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
      if (path === '/zh-Hans' || path === '/ja') expect(prefix, at).toBeNull();
      else {
        expect(prefix, at).not.toBeNull();
        expect(Math.min(...slots.map((slot) => slot.top)), at).toBeGreaterThanOrEqual(prefix!.bottom);
      }
      expect(Math.abs(conjunction.left - slots[0].right - 8), at).toBeLessThanOrEqual(1);
      expect(Math.max(...slots.map((slot) => slot.right)), at).toBeLessThanOrEqual(copy.right + 1);
      const midpoint = ({ top, bottom }: { top: number; bottom: number }) => (top + bottom) / 2;
      expect(Math.abs(midpoint(slots[0]) - midpoint(slots[1])), at).toBeLessThanOrEqual(1);
      expect(Math.abs(midpoint(slots[0]) - midpoint(conjunction)), at).toBeLessThanOrEqual(1);
      expect(suffixRects, at).toBe(path === '/zh-Hans' || path === '/ja' ? 1 : 0);
      expect(longestPair, at).toBeLessThanOrEqual(copy.width + 1);
      if (suffix) {
        const followsPair = Math.abs(midpoint(suffix) - midpoint(slots[1])) <= 1 && suffix.left > slots[1].right;
        expect(followsPair || suffix.top >= slots[1].bottom, at).toBe(true);
        expect(suffix.right, at).toBeLessThanOrEqual(copy.right + 1);
        expect(followsPair ? longestPair + 8 + suffix.width : suffix.width, at).toBeLessThanOrEqual(copy.width + 1);
      }
    }
    expect([...arrangements].sort()).toEqual(['inline', 'stacked']);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(active.first()).toHaveCSS('transition-duration', '0s');
  });
}
