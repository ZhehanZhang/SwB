'use client';

import { useEffect, useState } from 'react';
import { MarkTile, type MarkKind } from './mark-tile';
import { STEP, clock, trips } from '@/lib/live';

type Props = {
  stem: string;
  endings: string[];
  between: [string, string, string];
  names: Partial<Record<MarkKind, string>>;
};

const agentPairs = trips.map(([a, b], n) => ({ a, b, n })).filter(({ a, b }) => a !== 'slack' && a !== 'github' && b !== 'slack' && b !== 'github');

function useTick(ms: number) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = setInterval(() => setTick((value) => value + 1), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return tick;
}

function Slot({ options, current, names }: { options: MarkKind[]; current: MarkKind; names: Props['names'] }) {
  return (
    <span className="swb-slot">
      {options.map((kind) => (
        <span key={kind} className={`swb-chip ${kind === current ? 'swb-chip-on' : ''}`} aria-hidden={kind !== current}>
          <MarkTile kind={kind} className="swb-chip-mark" />
          {names[kind] ?? kind}
        </span>
      ))}
    </span>
  );
}

const starts = [...new Set(agentPairs.map(({ a }) => a))];
const ends = [...new Set(agentPairs.map(({ b }) => b))];

export function HeroTitle({ stem, endings, between, names }: Props) {
  const word = useTick(2400) % endings.length;
  useTick(250);
  const now = typeof window === 'undefined' ? 0 : clock();
  const step = Math.floor(now / STEP) % trips.length;
  const pair = [...agentPairs].reverse().find(({ n }) => n <= step) ?? agentPairs[agentPairs.length - 1];
  return (
    <>
      <h1 className="text-display-xl swb-hero-title">
        <span className="block">{stem}</span>
        <span className="swb-rotate" aria-live="off">
          {endings.map((text, index) => (
            <span key={text} className={`swb-rotate-item ${index === word ? 'swb-rotate-on' : ''}`} aria-hidden={index !== word}>{text}</span>
          ))}
        </span>
      </h1>
      <p className="swb-between mt-6 text-lg">
        {between[0]}<Slot options={starts} current={pair.a} names={names} />{between[1]}<Slot options={ends} current={pair.b} names={names} />{between[2]}
      </p>
    </>
  );
}
