import type { MarkKind } from '@/components/mark-tile';

export const trips: [MarkKind, MarkKind][] = [
  ['claude', 'grok-bot'],
  ['slack', 'claude'],
  ['grok-bot', 'capy'],
  ['capy', 'claude'],
  ['github', 'capy'],
  ['dots', 'claude'],
];

export const agentPairs = trips.map(([a, b], n) => ({ a, b, n }))
  .filter(({ a, b }) => a !== 'slack' && a !== 'github' && b !== 'slack' && b !== 'github');

export function agentPair(step: number) {
  return agentPairs.findLast(({ n }) => n <= step) ?? agentPairs[0];
}

export const LEG = 0.85;
export const STEP = 2.2;
export const CYCLE = trips.length * STEP;

let epoch: number | null = null;
export function clock() {
  if (epoch === null) epoch = performance.now();
  return (performance.now() - epoch) / 1000;
}
