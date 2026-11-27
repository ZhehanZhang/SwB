import { expect, test } from '@playwright/test';
import { CYCLE, STEP, trips } from '../../lib/live';

const pairs = trips.flatMap(([a, b], n) =>
  [a, b].some((name) => name === 'github' || name === 'slack') ? [] : [String(n)],
);

for (const [locale, path] of [['en', '/'], ['zh-Hans', '/zh-Hans'], ['zh-Hant', '/zh-Hant']]) {
  test(`${locale} tweens pill widths and keeps text gaps through a full cycle`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready.then(() => null));
    const line = page.locator('[data-hero-pair]');
    await expect(line).toHaveAttribute('data-hero-pair', pairs[0]);

    const result = await line.evaluate((root, deadlineMs) => new Promise<{
      seen: string[];
      settled: string[];
      widthRanges: number[];
      crossfades: boolean[];
      frameStep: number;
      verticalDrift: number;
      prefixDrift: number;
      gap: number;
      overflow: number;
    }>((resolve) => {
      const pair = root.querySelector('.swb-pair')!;
      const slots = [...root.querySelectorAll('.swb-slot')];
      const textRange = (element: Element | null) => {
        const node = element?.firstChild;
        if (!node?.textContent) return null;
        const { textContent: text } = node;
        const range = document.createRange();
        range.setStart(node, text.length - text.trimStart().length);
        range.setEnd(node, text.trimEnd().length);
        return range;
      };
      const prefix = textRange(root.querySelector(':scope > .swb-between-text:first-child'));
      const conjunction = textRange(pair.querySelector('.swb-between-text'))!;
      const suffix = textRange(root.querySelector(':scope > .swb-between-text:last-child'));
      const seen: string[] = [];
      const settled = new Set<string>();
      const crossfades = slots.map(() => false);
      const minWidths = slots.map(() => Infinity);
      const maxWidths = slots.map(() => -Infinity);
      const deadline = performance.now() + deadlineMs;
      let frameStep = 0;
      let verticalDrift = 0;
      let prefixDrift = 0;
      let gap = Infinity;
      let overflow = 0;
      let previousX: number[] | undefined;
      let baselineY: number[] | undefined;
      let baselinePrefix: number | undefined;

      function sample(now: number) {
        const index = root.getAttribute('data-hero-pair')!;
        if (seen.at(-1) !== index) seen.push(index);
        const lineRect = root.getBoundingClientRect();
        const pairRect = pair.getBoundingClientRect();
        const pillRects = slots.map((slot) => slot.getBoundingClientRect());
        const prefixRect = prefix?.getBoundingClientRect();
        const andRect = conjunction.getBoundingClientRect();
        const suffixRect = suffix?.getBoundingClientRect();
        const rects = [pairRect, ...pillRects, prefixRect, andRect, suffixRect].filter((rect) => rect !== undefined);
        const x = rects.flatMap(({ left, right }) => [left, right]);
        const y = [lineRect, ...rects, root.nextElementSibling!.getBoundingClientRect()].flatMap(({ top, bottom }) => [top, bottom]);
        baselineY ??= y;
        baselinePrefix ??= prefixRect?.left;
        if (previousX) frameStep = Math.max(frameStep, ...x.map((value, i) => Math.abs(value - previousX![i])));
        previousX = x;
        verticalDrift = Math.max(verticalDrift, ...y.map((value, i) => Math.abs(value - baselineY![i])));
        if (prefixRect) prefixDrift = Math.max(prefixDrift, Math.abs(prefixRect.left - baselinePrefix!));
        gap = Math.min(gap, andRect.left - pillRects[0].right, pillRects[1].left - andRect.right);
        if (prefixRect && prefixRect.bottom > pairRect.top) gap = Math.min(gap, pairRect.left - prefixRect.right);
        if (suffixRect && suffixRect.top < pairRect.bottom) gap = Math.min(gap, suffixRect.left - pairRect.right);
        overflow = Math.max(overflow, ...rects.flatMap(({ left, right }) => [lineRect.left - left, right - lineRect.right]));

        const atTarget = slots.map((slot, i) => {
          const { width } = pillRects[i];
          const active = slot.querySelector('.swb-chip-on')!;
          const opacities = [...slot.children].map((chip) => Number(getComputedStyle(chip).opacity));
          if (opacities.filter((opacity) => opacity > 0 && opacity < 1).length > 1) crossfades[i] = true;
          minWidths[i] = Math.min(minWidths[i], width);
          maxWidths[i] = Math.max(maxWidths[i], width);
          const { borderLeftWidth, borderRightWidth } = getComputedStyle(slot);
          const target = active.getBoundingClientRect().width + parseFloat(borderLeftWidth) + parseFloat(borderRightWidth);
          return Math.abs(width - target) < 0.1 && Number(getComputedStyle(active).opacity) === 1;
        }).every(Boolean);
        if (atTarget) settled.add(index);

        if ((seen.length > 1 && index === seen[0] && atTarget) || now >= deadline) {
          resolve({ seen, settled: [...settled], widthRanges: maxWidths.map((width, i) => width - minWidths[i]),
            crossfades, frameStep, verticalDrift, prefixDrift, gap, overflow });
          return;
        }
        requestAnimationFrame(sample);
      }

      requestAnimationFrame(sample);
    }), CYCLE * 1000 + 3000);

    expect(result.seen).toEqual([...pairs, pairs[0]]);
    expect(result.settled).toEqual(pairs);
    for (const range of result.widthRanges) expect(range).toBeGreaterThan(10);
    expect(result.crossfades).toEqual([true, true]);
    expect(result.frameStep).toBeLessThanOrEqual(2);
    expect(result.verticalDrift).toBeLessThanOrEqual(0.5);
    expect(result.prefixDrift).toBeLessThanOrEqual(0.5);
    expect(result.gap).toBeGreaterThanOrEqual(6);
    expect(result.overflow).toBeLessThanOrEqual(0.5);
  });

  test(`${locale} pills fit their text, icon and padding at 390px, 1200px and 1440px`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const slots = page.locator('.swb-slot');
    await expect(slots).toHaveCount(2);
    for (const width of [390, 1200, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const extraWidths = await slots.evaluateAll((nodes) => nodes.map((slot) => {
        const chip = slot.querySelector('.swb-chip-on')!;
        const icon = chip.querySelector('.swb-chip-mark')!.getBoundingClientRect();
        const name = document.createRange();
        name.selectNode(chip.lastChild!);
        const text = name.getBoundingClientRect();
        const { paddingLeft, paddingRight } = getComputedStyle(chip);
        const contentAndPadding = text.right - icon.left + parseFloat(paddingLeft) + parseFloat(paddingRight);
        return Math.abs(slot.getBoundingClientRect().width - contentAndPadding);
      }));
      for (const extra of extraWidths) expect(extra, `${locale} at ${width}px`).toBeLessThanOrEqual(2);
    }
  });
}

test('reduced motion holds the first hero pair', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await page.goto('/');
  const line = page.locator('[data-hero-pair]');
  await expect(line).toHaveAttribute('data-hero-pair', pairs[0]);
  await expect(line.locator('.swb-slot').first()).toHaveCSS('transition-duration', '0s');
  await page.clock.fastForward(STEP * 6000);
  await expect(line).toHaveAttribute('data-hero-pair', pairs[0]);
});
