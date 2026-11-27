import type { MarkKind } from '@/components/mark-tile';

export const trips: [MarkKind, MarkKind][] = [
  ['claude', 'grok-bot'],
  ['slack', 'claude'],
  ['grok-bot', 'capy'],
  ['capy', 'claude'],
  ['github', 'capy'],
  ['dots', 'claude'],
  ['claude', 'muse'],
  ['grok-bot', 'muse'],
];

export const LEG = 0.85;
export const STEP = 2.2;
export const CYCLE = trips.length * STEP;

let epoch: number | null = null;
export function clock() {
  if (epoch === null) epoch = performance.now();
  return (performance.now() - epoch) / 1000;
}
