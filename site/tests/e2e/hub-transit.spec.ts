import { expect, test } from '@playwright/test';

test('hub transit darkness eases in and out', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  const tracked = await board.evaluate((element) => new Promise<{ rise: boolean; fall: boolean; largestJump: number }>((resolve) => {
    const paths = [...element.querySelectorAll('.swb-transit')];
    const before = paths.map((path) => Number(getComputedStyle(path).opacity));
    let rise = false;
    let fall = false;
    let largestJump = 0;
    const end = performance.now() + 2800;
    const sample = (time: number) => {
      paths.forEach((path, i) => {
        const next = Number(getComputedStyle(path).opacity);
        const delta = next - before[i];
        largestJump = Math.max(largestJump, Math.abs(delta));
        if (next > 0.05 && next < 0.55) {
          rise ||= delta > 0.005;
          fall ||= delta < -0.005;
        }
        before[i] = next;
      });
      if (time < end) requestAnimationFrame(sample);
      else resolve({ rise, fall, largestJump });
    };
    requestAnimationFrame(sample);
  }));
  expect(tracked.rise).toBe(true);
  expect(tracked.fall).toBe(true);
  expect(tracked.largestJump).toBeLessThan(0.4);
});

test('reduced motion shows only still hub connections', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const paths = page.locator('.swb-board:visible .swb-transit');
  await expect(paths).toHaveCount(14);
  expect(await paths.evaluateAll((items) => items.every((path) =>
    getComputedStyle(path).opacity === '0' && !path.hasAttribute('data-flow')))).toBe(true);
});
