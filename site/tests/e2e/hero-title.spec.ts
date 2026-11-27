import { expect, test, type Locator } from '@playwright/test';
import { STEP, trips } from '../../lib/live';

const pairs = trips.flatMap(([a, b], n) =>
  [a, b].some((name) => name === 'github' || name === 'slack') ? [] : [String(n)],
);

function geometry(line: Locator) {
  return line.evaluate((root) => {
    const box = ({ x, y, width, height }: DOMRect) => [x, y, width, height];
    const conjunction = root.querySelector('.swb-pair .swb-between-text')!.firstChild!;
    const text = conjunction.textContent!;
    const word = document.createRange();
    word.setStart(conjunction, text.length - text.trimStart().length);
    word.setEnd(conjunction, text.trimEnd().length);
    const and = word.getBoundingClientRect();
    const pills = [...root.querySelectorAll('.swb-chip-on')].map((node) => node.getBoundingClientRect());
    return {
      pair: root.getAttribute('data-hero-pair')!,
      text: [...root.querySelectorAll('.swb-between-text')].map((node) => {
        const range = document.createRange();
        range.selectNodeContents(node);
        return [...range.getClientRects()].map(box);
      }),
      slots: [...root.querySelectorAll('.swb-slot')].map((node) => box(node.getBoundingClientRect())),
      pillWidths: pills.map(({ width }) => width),
      gaps: [and.left - pills[0].right, pills[1].left - and.right],
    };
  });
}

for (const [locale, path] of [['en', '/'], ['zh-Hans', '/zh-Hans'], ['zh-Hant', '/zh-Hant']]) {
  test(`${locale} keeps static text, slots and gaps fixed as pill widths change`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.clock.install();
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready.then(() => null));
    const line = page.locator('[data-hero-pair]');
    const baseline = await geometry(line);
    const seen = new Set<string>();
    const widths = [new Set<number>(), new Set<number>()];

    for (let i = 0; i <= trips.length * 3; i++) {
      const current = await geometry(line);
      expect(current.slots).toHaveLength(2);
      expect(current.pillWidths).toHaveLength(2);
      current.pillWidths.forEach((width, j) => widths[j].add(width));
      expect(current.text.map((rects) => rects.length)).toEqual(baseline.text.map((rects) => rects.length));
      for (const group of ['text', 'slots', 'gaps'] as const) {
        const initial = baseline[group].flat(2);
        current[group].flat(2).forEach((value, j) => {
          expect(Math.abs(value - initial[j]), `${group} moved in pair ${current.pair}`).toBeLessThanOrEqual(0.5);
        });
      }
      seen.add(current.pair);
      if (seen.size === pairs.length) break;
      await page.clock.fastForward(STEP * 500);
      await page.evaluate(() => new Promise(requestAnimationFrame));
    }

    expect([...seen].sort()).toEqual(pairs);
    widths.forEach((seenWidths) => expect(seenWidths.size).toBeGreaterThan(1));
  });
}

test('reduced motion holds the first hero pair', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await page.goto('/');
  const line = page.locator('[data-hero-pair]');
  await expect(line).toHaveAttribute('data-hero-pair', '0');
  await page.clock.fastForward(STEP * 6000);
  await expect(line).toHaveAttribute('data-hero-pair', '0');
});
