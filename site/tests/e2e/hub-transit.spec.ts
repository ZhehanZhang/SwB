import { expect, test } from '@playwright/test';

test('hub transit fades in and out on a non-active spoke', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  const slack = board.locator('.swb-transit[data-app="slack"]');
  await expect(slack).toHaveCSS('transition-duration', '0.3s, 0.3s, 0.3s');
  const tracked = await slack.evaluate((path) => new Promise<{
    rise: boolean; fall: boolean; minWidth: number; maxWidth: number; contrast: string;
  }>((resolve) => {
    let before = Number(getComputedStyle(path).opacity);
    let rise = false;
    let fall = false;
    let minWidth = Infinity;
    let maxWidth = 0;
    let contrast = '';
    const end = performance.now() + 15_000;
    const sample = (time: number) => {
      const style = getComputedStyle(path);
      const opacity = Number(style.opacity);
      if (opacity > 0.08 && opacity < 1) {
        rise ||= opacity > before;
        fall ||= opacity < before;
      }
      if (path.hasAttribute('data-flow')) {
        const width = parseFloat(style.strokeWidth);
        minWidth = Math.min(minWidth, width);
        maxWidth = Math.max(maxWidth, width);
        if (opacity === 1) contrast = style.stroke;
      }
      before = opacity;
      if ((rise && fall && contrast && opacity <= 0.08) || time >= end) {
        resolve({ rise, fall, minWidth, maxWidth, contrast });
      } else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }));
  expect(tracked.rise).toBe(true);
  expect(tracked.fall).toBe(true);
  expect(tracked.minWidth).toBeGreaterThanOrEqual(2.8);
  expect(tracked.maxWidth).toBeLessThanOrEqual(3.4);
  expect(tracked.contrast).toBe('rgb(39, 39, 42)');
});

test('the same DOM pulse moves over 300 ms and crosses the hub', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  await expect(board.locator('.swb-hub-pulse:visible').first()).toBeVisible();
  const hub = board.locator('.swb-hub');
  const before = await hub.boundingBox();
  const travel = await board.evaluate((element) => new Promise<{ movement: number; crossed: boolean }>((resolve) => {
    const svg = element.querySelector('svg')!;
    const { width, height } = svg.viewBox.baseVal;
    const pulses = [...svg.querySelectorAll('circle.swb-hub-pulse')];
    const states = pulses.map(() => ({ start: null as { x: number; y: number; time: number } | null, phase: 0 }));
    let movement = 0;
    let crossed = false;
    const end = performance.now() + 4_000;
    const sample = (time: number) => {
      pulses.forEach((pulse, i) => {
        const state = states[i];
        if (!pulse.hasAttribute('data-flow')) {
          state.start = null;
          state.phase = 0;
          return;
        }
        const x = Number(pulse.getAttribute('cx'));
        const y = Number(pulse.getAttribute('cy'));
        const distance = Math.hypot(x - width / 2, y - height / 2);
        if (distance > 100 && state.phase === 0) state.phase = 1;
        if (distance < 20 && state.phase === 1) state.phase = 2;
        if (distance > 100 && state.phase === 2) crossed = true;
        if (!state.start) state.start = { x, y, time };
        else if (time - state.start.time >= 300) {
          movement = Math.max(movement, Math.hypot(x - state.start.x, y - state.start.y));
          state.start = { x, y, time };
        }
      });
      if ((crossed && movement > 50) || time >= end) resolve({ crossed, movement });
      else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }));
  expect(travel.movement).toBeGreaterThan(50);
  expect(travel.crossed).toBe(true);
  expect(await hub.boundingBox()).toEqual(before);
});

test('reduced motion shows only still hub connections', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  const active = board.locator('.swb-transit[data-active]');
  await expect(active).toHaveCount(2);
  await expect(board.locator('.swb-transit[data-flow]')).toHaveCount(0);
  await expect(board.locator('.swb-hub-pulse:visible')).toHaveCount(0);
  const pair = () => active.evaluateAll((items) => items.map((path) => path.getAttribute('data-app')).sort());
  expect(await pair()).toEqual(['claude', 'grok-bot']);
  await page.clock.fastForward(5_000);
  expect(await pair()).toEqual(['claude', 'grok-bot']);
  await expect(board.locator('.swb-hub-pulse:visible')).toHaveCount(0);
});

for (const theme of ['light', 'dark'] as const) {
  test(`${theme} hub keeps idle spokes faint and emphasizes the active pair`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto('/');
    await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${theme}\\b`));
    const paths = page.locator('.swb-board:visible .swb-transit');
    const spokes = await paths.evaluateAll((items) => items.map((path) => {
      const { strokeWidth, stroke, opacity } = getComputedStyle(path);
      return { active: path.hasAttribute('data-active'), width: parseFloat(strokeWidth), stroke, opacity: Number(opacity) };
    }));
    const active = spokes.filter((spoke) => spoke.active);
    const idle = spokes.filter((spoke) => !spoke.active);
    expect(active).toHaveLength(2);
    expect(idle).toHaveLength(12);
    for (const spoke of idle) {
      expect(spoke.opacity).toBeLessThanOrEqual(0.08);
    }
    for (const spoke of active) {
      expect(spoke.width).toBeGreaterThanOrEqual(2);
      expect(spoke.stroke).toBe(theme === 'light' ? 'rgb(82, 82, 91)' : 'rgb(161, 161, 170)');
      expect(spoke.opacity).toBe(1);
    }
  });
}
