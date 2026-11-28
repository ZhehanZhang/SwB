import { expect, test } from '@playwright/test';

test('hub transit fades in and out on a non-active spoke', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  const slack = board.locator('.swb-transit[data-app="slack"]');
  await expect(slack).toHaveCSS('transition-duration', '0.3s, 0.3s');
  const tracked = await slack.evaluate((path) => new Promise<{
    rise: boolean; fall: boolean; contrast: string; stable: boolean;
  }>((resolve) => {
    const shape = path.getAttribute('d');
    let before = Number(getComputedStyle(path).opacity);
    let rise = false;
    let fall = false;
    let contrast = '';
    let stable = true;
    const end = performance.now() + 15_000;
    const sample = (time: number) => {
      const style = getComputedStyle(path);
      const opacity = Number(style.opacity);
      if (opacity > 0.08 && opacity < 1) {
        rise ||= opacity > before;
        fall ||= opacity < before;
      }
      if (path.hasAttribute('data-flow') && opacity === 1) contrast = style.fill;
      stable &&= path.getAttribute('d') === shape;
      before = opacity;
      if ((rise && fall && contrast && opacity <= 0.08) || time >= end) {
        resolve({ rise, fall, contrast, stable });
      } else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }));
  expect(tracked.rise).toBe(true);
  expect(tracked.fall).toBe(true);
  expect(tracked.stable).toBe(true);
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
  const travel = await board.evaluate((element) => new Promise<{ movement: number; crossed: boolean; followsCord: boolean }>((resolve) => {
    const svg = element.querySelector('svg')!;
    const { width, height } = svg.viewBox.baseVal;
    const pulses = [...svg.querySelectorAll('circle.swb-hub-pulse')];
    const states = pulses.map(() => ({ start: null as { x: number; y: number; time: number } | null, phase: 0 }));
    let movement = 0;
    let crossed = false;
    let followsCord = true;
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
        followsCord &&= [...svg.querySelectorAll<SVGPathElement>('.swb-transit[data-flow]')]
          .some((cord) => cord.isPointInFill(new DOMPoint(x, y)));
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
      if ((crossed && movement > 50) || time >= end) resolve({ crossed, movement, followsCord });
      else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }));
  expect(travel.movement).toBeGreaterThan(50);
  expect(travel.crossed).toBe(true);
  expect(travel.followsCord).toBe(true);
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
  await expect(board.getByRole('link', { name: 'Claude', exact: true })).toHaveCSS('animation-name', 'none');
  await expect(board.getByRole('link', { name: 'Claude', exact: true }).locator('.swb-mark-tile')).toHaveCSS('animation-name', 'none');
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
      const { fill, stroke, opacity } = getComputedStyle(path);
      return { active: path.hasAttribute('data-active'), fill, stroke, opacity: Number(opacity) };
    }));
    const active = spokes.filter((spoke) => spoke.active);
    const idle = spokes.filter((spoke) => !spoke.active);
    expect(active).toHaveLength(2);
    expect(idle).toHaveLength(12);
    for (const spoke of idle) {
      expect(spoke.opacity).toBeLessThanOrEqual(0.08);
    }
    for (const spoke of active) {
      expect(spoke.stroke).toBe('none');
      expect(spoke.fill).toBe(theme === 'light' ? 'rgb(82, 82, 91)' : 'rgb(161, 161, 170)');
      expect(spoke.opacity).toBe(1);
    }
  });
}

test('the visible connector tapers from SwB to the app', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  const cord = board.locator('.swb-transit[data-app="claude"]');
  await expect(cord).toHaveAttribute('d', /^M [^A-Z]+ C [^A-Z]+ C [^A-Z]+ C [^A-Z]+ C [^A-Z]+ Z$/);
  const widths = await cord.evaluate((path) => {
    const svg = (path as SVGPathElement).ownerSVGElement!;
    const matrix = svg.getScreenCTM()!;
    const { width } = svg.viewBox.baseVal;
    const { width: hub } = svg.parentElement!.querySelector('.swb-hub')!.getBoundingClientRect();
    const { width: app } = svg.parentElement!.querySelector('.swb-node-invite .swb-mark-tile')!.getBoundingClientRect();
    const points = path.getAttribute('d')!.split(/[MCZ\s]+/).filter(Boolean).map(Number);
    const edge = (t: number, reverse: boolean) => {
      const u = 1 - t;
      const weights = [u ** 3, 3 * u * u * t, 3 * u * t * t, t ** 3];
      const coordinate = (axis: number) => weights.reduce((sum, weight, i) =>
        sum + weight * points[(reverse ? 9 - i : i) * 2 + axis], 0);
      return new DOMPoint(coordinate(0), coordinate(1)).matrixTransform(matrix);
    };
    const distance = (a: DOMPoint, b: DOMPoint) => Math.hypot(b.x - a.x, b.y - a.y);
    const center = (t: number) => {
      const a = edge(t, false);
      const b = edge(t, true);
      return new DOMPoint((a.x + b.x) / 2, (a.y + b.y) / 2);
    };
    const origin = center(0);
    const end = center(1);
    const visible = [];
    for (let i = 1; i < 200; i++) {
      const t = i / 200;
      const mid = center(t);
      if (distance(mid, origin) < hub / 2 || distance(mid, end) < app / 2) continue;
      const ahead = center(t + 0.005);
      const a = edge(t, false);
      const b = edge(t, true);
      const perpendicular = Math.abs((ahead.x - mid.x) * (b.y - a.y) - (ahead.y - mid.y) * (b.x - a.x)) / distance(mid, ahead);
      visible.push(perpendicular);
    }
    return { hub: visible.at(0), app: visible.at(-1), offAxis: (distance(origin, center(.5)) + distance(center(.5), end) - distance(origin, end)) / (width * matrix.a) };
  });
  expect(widths.hub).toBeGreaterThanOrEqual(6);
  expect(widths.hub).toBeLessThanOrEqual(7);
  expect(widths.app).toBeGreaterThanOrEqual(1.3);
  expect(widths.app).toBeLessThanOrEqual(1.9);
  expect(widths.offAxis).toBeGreaterThan(.005);
});
