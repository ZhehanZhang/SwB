import { expect, test } from '@playwright/test';

test('hub transit fades in and out on a non-active spoke', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  await board.scrollIntoViewIfNeeded();
  const paths = board.locator('.swb-transit');
  await expect(paths.first()).toHaveCSS('transition-duration', '0.3s, 0.3s, 0.3s');
  const tracked = await board.evaluate((element) => new Promise<{
    rise: boolean; fall: boolean; contrast: string; stable: boolean;
  }>((resolve) => {
    const paths = [...element.querySelectorAll('.swb-transit')];
    const states = paths.map((path) => ({
      shape: path.getAttribute('d'), before: Number(getComputedStyle(path).opacity),
      rise: false, fall: false, contrast: '', stable: true,
    }));
    const end = performance.now() + 8_000;
    const sample = (time: number) => {
      paths.forEach((path, i) => {
        const state = states[i];
        const style = getComputedStyle(path);
        const opacity = Number(style.opacity);
        if (opacity > 0.08 && opacity < 1) {
          state.rise ||= opacity > state.before;
          state.fall ||= opacity < state.before;
        }
        if (path.hasAttribute('data-flow') && opacity === 1 && style.stroke === 'rgb(39, 39, 42)') state.contrast = style.stroke;
        state.stable &&= path.getAttribute('d') === state.shape;
        state.before = opacity;
      });
      const finished = states.find(({ rise, fall, contrast, before }) => rise && fall && contrast && before <= 0.08);
      if (finished || time >= end) resolve(finished ?? states.find(({ rise }) => rise) ?? states[0]);
      else requestAnimationFrame(sample);
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
          .some((cord) => cord.isPointInStroke(new DOMPoint(x, y)));
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
  const first = await pair();
  const pills = await page.locator('.swb-chip-on').evaluateAll((items) => items.map((chip) => chip.getAttribute('data-hero-app')).sort());
  expect(first).toEqual(pills);
  await page.clock.fastForward(5_000);
  expect(await pair()).toEqual(first);
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
      expect(spoke.stroke).toBe(theme === 'light' ? 'rgb(82, 82, 91)' : 'rgb(161, 161, 170)');
      expect(spoke.fill).toBe('none');
      expect(spoke.opacity).toBe(1);
    }
  });
}

test('the visible ring retains the live quadratic connectors and mark sizes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  const compact = page.viewportSize()!.width < 640;
  const marks = await board.evaluate((element) => {
    const paths = [...element.querySelectorAll<SVGPathElement>('.swb-transit')];
    const { width } = element.getBoundingClientRect();
    const { height } = element.querySelector('svg')!.viewBox.baseVal;
    return {
      height,
      hub: element.querySelector('.swb-hub')!.getBoundingClientRect().width / width,
      app: element.querySelector('.swb-node .swb-mark-tile')!.getBoundingClientRect().width / width,
      strokes: paths.map((path) => path.getAttribute('d')!.split(/\s+/).slice(1)),
    };
  });
  const center = marks.height / 2;
  const radius = compact ? 338 : 282;
  expect(marks.height).toBe(compact ? 1000 : 820);
  expect(marks.hub).toBeCloseTo(compact ? .17 : .117, 3);
  expect(marks.app).toBeCloseTo(compact ? .12 : .086, 3);
  expect(marks.strokes).toHaveLength(14);
  for (const [sx, sy, command, qx, qy, x, y, ...rest] of marks.strokes) {
    expect(rest).toEqual([]);
    expect(command).toBe('Q');
    expect(Number(sx)).toBe(500);
    expect(Number(sy)).toBe(center);
    const dx = Number(x) - 500;
    const dy = Number(y) - center;
    expect(Math.hypot(dx, dy)).toBeCloseTo(radius, 6);
    expect(Number(qx)).toBeCloseTo(500 + dx / 2 - dy * .14, 6);
    expect(Number(qy)).toBeCloseTo(center + dy / 2 + dx * .14, 6);
  }
});

test('the setup caption sits directly below the ring', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const caption = page.locator('.swb-hero figcaption');
  await expect(caption).toHaveText('Select an app to set it up.');
  const gap = await caption.evaluate((label) => {
    const board = label.parentElement!.querySelector('.swb-board:not(.hidden)');
    const visible = [...label.parentElement!.querySelectorAll('.swb-board')].find((node) => node.getClientRects().length) ?? board!;
    const ringBottom = Math.max(...[...visible.querySelectorAll('.swb-node, .swb-node-label')]
      .filter((node) => node.getClientRects().length).map((node) => node.getBoundingClientRect().bottom));
    return label.getBoundingClientRect().top - ringBottom;
  });
  expect(gap).toBeGreaterThanOrEqual(6);
  expect(gap).toBeLessThanOrEqual(22);
});
