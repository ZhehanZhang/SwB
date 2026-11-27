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
  const [word, setWord] = useState(0);
  const [step, setStep] = useState(0);
  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let timer: number | undefined;
    function onMotionChange() {
      window.clearInterval(timer);
      if (motion.matches) {
        setWord(0);
        setStep(0);
        return;
      }
      timer = window.setInterval(() => {
        const now = clock();
        setWord(Math.floor(now / 2.4) % endings.length);
        setStep(Math.floor(now / STEP) % trips.length);
      }, 100);
    }
    onMotionChange();
    motion.addEventListener('change', onMotionChange);
    return () => {
      window.clearInterval(timer);
      motion.removeEventListener('change', onMotionChange);
    };
  }, [endings.length]);
  const pair = agentPairs.findLast(({ n }) => n <= step) ?? agentPairs[0];
  return (
    <>
      <h1 id="landing-heading" className="text-display-xl swb-hero-title">
        <span className="block text-balance">{stem}</span>
        <span className="swb-rotate" aria-live="off">
          {endings.map((text, index) => (
            <span key={text} className={`swb-rotate-item ${index === word ? 'swb-rotate-on' : ''}`} aria-hidden={index !== word}>{text}</span>
          ))}
        </span>
      </h1>
      <p className="swb-between mt-3 text-base" data-hero-pair={pair.n}>
        {between[0] && <span className="swb-between-text">{between[0]}</span>}
        <span className="swb-pair">
          <Slot options={starts} current={pair.a} names={names} />
          <span className="swb-between-text">{between[1]}</span>
          <Slot options={ends} current={pair.b} names={names} />
        </span>
        {between[2] && <span className="swb-between-text">{between[2]}</span>}
      </p>
    </>
  );
}
