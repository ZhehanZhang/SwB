import type { Page } from '@playwright/test';
import { PAIR_ACTIVE_MS, PAIR_RELEASE_MS } from '../../lib/live';

export async function seedHero(page: Page, seed = 47) {
  await page.addInitScript((state) => {
    Math.random = () => {
      state = (Math.imul(1664525, state) + 1013904223) >>> 0;
      return state / 0x100000000;
    };
  }, seed);
}

export async function pauseHeroClock(page: Page) {
  const time = new Date('2026-01-01T00:00:00Z');
  await page.clock.install({ time });
  await page.clock.pauseAt(new Date(time.getTime() + 1000));
}

export async function advanceHeroPair(page: Page) {
  await page.clock.fastForward(PAIR_ACTIVE_MS);
  await page.clock.fastForward(PAIR_RELEASE_MS);
}
