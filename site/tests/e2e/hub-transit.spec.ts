import { expect, test } from '@playwright/test';

test('hub transit darkness eases in and out', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  const tracked = await board.evaluate((element) => new Promise<{ rise: boolean; fall: boolean; largestJump: number }>((resolve) => {
    const paths = [...element.querySelectorAll('.swb-transit')];
    const before = paths.map((path) => parseFloat(getComputedStyle(path).strokeWidth));
    let rise = false;
    let fall = false;
    let largestJump = 0;
    const end = performance.now() + 3600;
    const sample = (time: number) => {
      paths.forEach((path, i) => {
        const next = parseFloat(getComputedStyle(path).strokeWidth);
        const delta = next - before[i];
        largestJump = Math.max(largestJump, Math.abs(delta));
        if (next > 2.85 && next < 3.35) {
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
  expect(tracked.largestJump).toBeLessThan(0.8);
});

test('reduced motion shows only still hub connections', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const paths = page.locator('.swb-board:visible .swb-transit');
  await expect(paths).toHaveCount(14);
  expect(await paths.evaluateAll((items) => items.every((path) =>
    getComputedStyle(path).opacity === '1' && !path.hasAttribute('data-flow')))).toBe(true);
});

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} hub spokes stay visible and emphasize the current pair`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto('/');
    await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${theme}\\b`));
    const paths = page.locator('.swb-board:visible .swb-transit');
    const spokes = await paths.evaluateAll((items) => items.map((path) => {
      const { strokeWidth, stroke, opacity, strokeOpacity } = getComputedStyle(path);
      return { app: path.getAttribute('data-app'), active: path.hasAttribute('data-active'), width: parseFloat(strokeWidth), stroke, opacity, strokeOpacity };
    }));
    const active = spokes.filter((spoke) => spoke.active);
    const idle = spokes.filter((spoke) => !spoke.active);
    expect(active.map(({ app }) => app).sort()).toEqual(['claude', 'grok-bot']);
    expect(idle).toHaveLength(12);
    for (const spoke of idle) {
      expect(spoke.width).toBeGreaterThanOrEqual(1.75);
      expect(spoke.stroke).toBe(theme === 'light' ? 'rgb(161, 161, 170)' : 'rgb(96, 96, 105)');
      expect(spoke.opacity).toBe('1');
      expect(spoke.strokeOpacity).toBe('1');
    }
    for (const spoke of active) {
      expect(spoke.width).toBeGreaterThan(Math.max(...idle.map(({ width }) => width)));
      expect(spoke.stroke).toBe(theme === 'light' ? 'rgb(82, 82, 91)' : 'rgb(161, 161, 170)');
      expect(spoke.opacity).toBe('1');
    }
  });
}
