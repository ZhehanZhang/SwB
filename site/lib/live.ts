export const heroApps = [
  { kind: 'slack', label: 'Slack' },
  { kind: 'claude', label: 'Claude' },
  { kind: 'capy', label: 'Capy Captain' },
  { kind: 'dots', label: 'Dots' },
  { kind: 'discord', label: 'Discord' },
  { kind: 'chatgpt', label: 'ChatGPT' },
  { kind: 'codex', label: 'Codex' },
  { kind: 'github', label: 'GitHub' },
  { kind: 'gemini', label: 'Gemini' },
  { kind: 'claude-code', label: 'Claude Code' },
  { kind: 'grok', label: 'Grok' },
  { kind: 'telegram', label: 'Telegram' },
  { kind: 'grok-bot', label: 'Grok Bot' },
  { kind: 'muse', label: 'Muse' },
] as const;

export type HeroApp = typeof heroApps[number]['kind'];
export type HeroPair = { a: HeroApp; b: HeroApp; n: number };
type Pair = [number, number];

export const PAIR_ACTIVE_MS = 1600;
export const PAIR_RELEASE_MS = 600;
export const PAIR_LEG_MS = 650;
export const PAIRS_PER_ROUND = heroApps.length / 2;

function arrange(pool: number[], avoid?: Pair): Pair[] | undefined {
  if (!pool.length) return [];
  const i = avoid ? pool.findIndex((app) => !avoid.includes(app)) : 0;
  if (i < 0) return;
  const a = pool[i];
  for (let j = 0; j < pool.length; j++) {
    const b = pool[j];
    const distance = Math.abs(a - b);
    if (distance <= 1 || distance === heroApps.length - 1 || avoid?.includes(b)) continue;
    const next = arrange(pool.filter((_, k) => k !== i && k !== j));
    if (next) return [[a, b], ...next];
  }
}

export function createHeroSequence(random = Math.random, initial?: HeroPair) {
  let round: Pair[] = [];
  let previous: Pair | undefined;
  let n = 0;
  return (): HeroPair => {
    if (!round.length) {
      const pool = heroApps.map((_, i) => i);
      for (let i = pool.length - 1; i > 0; i--) {
        const j = Math.floor(random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      const first: Pair | undefined = initial && n === 0
        ? [heroApps.findIndex(({ kind }) => kind === initial.a), heroApps.findIndex(({ kind }) => kind === initial.b)]
        : undefined;
      const pairs = first ? arrange(pool.filter((app) => !first.includes(app))) : arrange(pool, previous);
      if (!pairs) throw new Error('Could not arrange the hero apps');
      round = first ? [first, ...pairs] : pairs;
    }
    previous = round.shift()!;
    return { a: heroApps[previous[0]].kind, b: heroApps[previous[1]].kind, n: ++n };
  };
}
