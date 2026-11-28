import { expect, test } from '@playwright/test';
import { heroApps, PAIR_ACTIVE_MS, PAIR_RELEASE_MS, PAIRS_PER_ROUND, type HeroApp } from '../../lib/live';
import { pauseHeroClock, seedHero } from './hero-seed';

test('a seeded hero draws every app fairly, matches its pills and fully releases every pair', async ({ page }) => {
  test.setTimeout(90_000);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await seedHero(page);
  await pauseHeroClock(page);
  await page.goto('/');
  const board = page.locator('.swb-board:visible');
  const active = board.locator('.swb-transit[data-active]');
  const flowing = board.locator('[data-flow]');
  const caption = page.locator('[data-hero-pair]');
  const chips = caption.locator('.swb-chip-on');
  const appearances = new Map(heroApps.map(({ kind }) => [kind, 0]));
  const appOrder = heroApps.map(({ kind }) => kind);
  const cycles = 210;
  let previous: HeroApp[] = [];
  const round = new Set<HeroApp>();

  for (let i = 0; i < cycles; i++) {
    await expect(caption).toHaveAttribute('data-hero-pair', String(i + 1));
    await expect(active).toHaveCount(2);
    const painted = await active.evaluateAll((paths) => paths.map((path) => path.getAttribute('data-app') as HeroApp));
    const named = await chips.evaluateAll((items) => items.map((chip) => chip.getAttribute('data-hero-app')));
    expect([...named].sort()).toEqual([...painted].sort());
    expect(painted.some((app) => previous.includes(app))).toBe(false);
    const distance = Math.abs(appOrder.indexOf(painted[0]) - appOrder.indexOf(painted[1]));
    expect(distance).toBeGreaterThan(1);
    expect(distance).toBeLessThan(heroApps.length - 1);
    for (const app of painted) {
      expect(round.has(app)).toBe(false);
      round.add(app);
      appearances.set(app, appearances.get(app)! + 1);
    }
    if ((i + 1) % PAIRS_PER_ROUND === 0) {
      expect([...round].sort()).toEqual([...appOrder].sort());
      round.clear();
    }
    previous = painted;
    await page.clock.fastForward(PAIR_ACTIVE_MS);
    await expect(active).toHaveCount(0);
    await expect(flowing).toHaveCount(0);
    await page.clock.fastForward(PAIR_RELEASE_MS);
  }
  expect([...appearances.values()]).toEqual(appOrder.map(() => cycles / PAIRS_PER_ROUND));
});

test('the server paints the same active pair on the ring and pills before hydration', async ({ page }) => {
  await page.route('**/_next/static/**/*.js', (route) => route.abort());
  for (const path of ['/', '/zh-Hans', '/de']) {
    await page.goto(path);
    const active = await page.locator('.swb-board:visible .swb-transit[data-active]').evaluateAll((paths) =>
      paths.map((path) => path.getAttribute('data-app')));
    const pills = await page.locator('.swb-chip-on').evaluateAll((chips) =>
      chips.map((chip) => chip.getAttribute('data-hero-app')));
    expect(active).toHaveLength(2);
    expect(active.sort()).toEqual(pills.sort());
  }
});
