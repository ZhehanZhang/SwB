import { expect, test } from '@playwright/test';
import { PAIR_ACTIVE_MS, PAIR_RELEASE_MS, PAIRS_PER_ROUND } from '../../lib/live';
import { advanceHeroPair, pauseHeroClock, seedHero } from './hero-seed';

const pairs = Array.from({ length: PAIRS_PER_ROUND }, (_, i) => String(i + 1));
const nextRound = String(PAIRS_PER_ROUND + 1);
const roundDuration = (PAIR_ACTIVE_MS + PAIR_RELEASE_MS) * PAIRS_PER_ROUND;

for (const [locale, path] of [['en', '/'], ['zh-Hans', '/zh-Hans'], ['zh-Hant', '/zh-Hant']]) {
  test(`${locale} tweens pill widths and keeps text gaps through a full cycle`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await seedHero(page);
    await pauseHeroClock(page);
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready.then(() => null));
    const line = page.locator('[data-hero-pair]');
    const frames = [];
    for (const index of [...pairs, nextRound]) {
      await expect(line).toHaveAttribute('data-hero-pair', index);
      const samples = await line.evaluate((root) => {
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
        const conjunctionElement = pair.querySelector('.swb-between-text')!;
        const conjunction = textRange(conjunctionElement)!;
        const suffix = textRange(pair.querySelector('.swb-pair-suffix'));
        const transitions = pair.getAnimations({ subtree: true });
        const duration = Math.max(0, ...transitions.map((transition) => transition.effect!.getComputedTiming().endTime));
        const frameTime = 1000 / 60;
        transitions.forEach((transition) => transition.pause());

        const samples = [];
        for (let step = 0; step <= Math.ceil(duration / frameTime); step++) {
          for (const transition of transitions) transition.currentTime = Math.min(step * frameTime, duration);
          const lineRect = root.getBoundingClientRect();
          const pillRects = slots.map((slot) => slot.getBoundingClientRect());
          const prefixRect = prefix?.getBoundingClientRect();
          const andRect = conjunction.getBoundingClientRect();
          const suffixRect = suffix?.getBoundingClientRect();
          const rects = [...pillRects, prefixRect, andRect, suffixRect].filter((rect) => rect !== undefined);
          const x = rects.flatMap(({ left, right }) => [left, right]);
          const y = [lineRect, ...rects, root.nextElementSibling!.getBoundingClientRect()].flatMap(({ top, bottom }) => [top, bottom]);
          let gap = andRect.left - pillRects[0].right;
          if (pillRects[1].top < andRect.bottom) gap = Math.min(gap, pillRects[1].left - andRect.right);
          if (prefixRect && prefixRect.bottom > pillRects[0].top) gap = Math.min(gap, pillRects[0].left - prefixRect.right);
          if (suffixRect && suffixRect.top < pillRects[1].bottom) gap = Math.min(gap, suffixRect.left - pillRects[1].right);

          const crossfades = slots.map((slot) => [...slot.children].filter((chip) => {
            const opacity = Number(getComputedStyle(chip).opacity);
            return opacity > 0 && opacity < 1;
          }).length > 1);
          const settled = slots.every((slot, i) => {
            const active = slot.querySelector('.swb-chip-on')!;
            const { borderLeftWidth, borderRightWidth } = getComputedStyle(slot);
            const target = active.getBoundingClientRect().width + parseFloat(borderLeftWidth) + parseFloat(borderRightWidth);
            return Math.abs(pillRects[i].width - target) < 0.1 && Number(getComputedStyle(active).opacity) === 1;
          });
          samples.push({
            x, y, gap, crossfades, settled, prefix: prefixRect?.left ?? 0, widths: pillRects.map(({ width }) => width),
            leadGapError: Math.abs(conjunctionElement.getBoundingClientRect().left - pillRects[0].right - 8),
            overflow: Math.max(0, ...rects.flatMap(({ left, right }) => [lineRect.left - left, right - lineRect.right])),
          });
        }
        transitions.forEach((transition) => transition.finish());
        return samples;
      });
      expect(samples.at(-1)?.settled, `${locale} pair ${index} settles`).toBe(true);
      frames.push(...samples);
      if (index !== nextRound) await advanceHeroPair(page);
    }

    const [baseline] = frames;
    for (const slot of [0, 1]) {
      const widths = frames.map((frame) => frame.widths[slot]);
      expect(Math.max(...widths) - Math.min(...widths)).toBeGreaterThan(10);
      expect(frames.some((frame) => frame.crossfades[slot])).toBe(true);
    }
    expect(Math.max(...frames.slice(1).map((frame, i) =>
      Math.max(...frame.x.map((x, j) => Math.abs(x - frames[i].x[j])))))).toBeLessThanOrEqual(2);
    expect(Math.max(...frames.map((frame) => Math.max(...frame.y.map((y, i) => Math.abs(y - baseline.y[i])))))).toBeLessThanOrEqual(0.5);
    expect(Math.max(...frames.map((frame) => Math.abs(frame.prefix - baseline.prefix)))).toBeLessThanOrEqual(0.5);
    expect(Math.min(...frames.map((frame) => frame.gap))).toBeGreaterThanOrEqual(6);
    expect(Math.max(...frames.map((frame) => frame.leadGapError))).toBeLessThanOrEqual(1);
    expect(Math.max(...frames.map((frame) => frame.overflow))).toBeLessThanOrEqual(0.5);
  });

  test(`${locale} pills fit their text, icon and padding at each target width`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const slots = page.locator('.swb-slot');
    await expect(slots).toHaveCount(2);
    for (const width of [390, 1024, 1200, 1440]) {
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

for (const [locale, path] of [['en', '/'], ['zh-Hans', '/zh-Hans'], ['zh-Hant', '/zh-Hant'], ['de', '/de'], ['ja', '/ja']]) {
  test(`${locale} keeps the hero subtitle in intact groups for every mobile pair`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await seedHero(page);
    await pauseHeroClock(page);
    for (const width of [320, 340, 360, 380, 390, 400, 420, 430]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      await page.addStyleTag({ content: '.swb-pair, .swb-chip { transition: none; }' });
      const subtitle = page.locator('[data-hero-pair]');
      const seen = new Set<string>();
      for (const pair of pairs) {
        await expect(subtitle).toHaveAttribute('data-hero-pair', pair);
        seen.add(pair);
        const group = await subtitle.evaluate((root) => {
          const line = (element: Element) => {
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            const range = document.createRange();
            const rects: { top: number; left: number; right: number }[] = [];
            for (let text = walker.nextNode(); text; text = walker.nextNode()) {
              if (!text.textContent?.trim()) continue;
              range.selectNodeContents(text);
              rects.push(...Array.from(range.getClientRects(), ({ top, left, right }) => ({ top, left, right })));
            }
            return rects;
          };
          const parts = [...root.querySelectorAll('.swb-pair-row .swb-chip-on, .swb-pair-lead .swb-between-text')];
          const suffix = root.querySelector('.swb-pair-suffix');
          const row = root.querySelector('.swb-pair-row')!.getBoundingClientRect();
          const { left, right } = root.getBoundingClientRect();
          return { pair: parts.flatMap(line), suffix: suffix ? line(suffix) : [], rowBottom: row.bottom, left, right };
        });
        const at = `${locale} at ${width}px with pair ${pair}`;
        expect(group.pair, at).toHaveLength(3);
        expect(group.suffix, at).toHaveLength(locale === 'en' || locale === 'de' ? 0 : 1);
        const tops = group.pair.map(({ top }) => top);
        expect(Math.max(...tops) - Math.min(...tops), at).toBeLessThanOrEqual(4);
        for (const rect of [...group.pair, ...group.suffix]) {
          expect(rect.left, at).toBeGreaterThanOrEqual(group.left - 1);
          expect(rect.right, at).toBeLessThanOrEqual(group.right + 1);
        }
        if (group.suffix.length) {
          const [suffix] = group.suffix;
          const followsPair = Math.abs(suffix.top - tops[2]) <= 4 && suffix.left > group.pair[2].right;
          expect(followsPair || suffix.top >= group.rowBottom, at).toBe(true);
        }
        await advanceHeroPair(page);
      }
      expect([...seen]).toEqual(pairs);
    }
  });
}

test('reduced motion holds the first hero pair', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await page.goto('/');
  const line = page.locator('[data-hero-pair]');
  await expect(line).toHaveAttribute('data-hero-pair', pairs[0]);
  await expect(line.locator('.swb-chip-on').first()).toHaveCSS('transition-duration', '0s');
  await page.clock.fastForward(roundDuration);
  await expect(line).toHaveAttribute('data-hero-pair', pairs[0]);
});
