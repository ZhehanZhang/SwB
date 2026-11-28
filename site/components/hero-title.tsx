'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { MarkTile, type MarkKind } from './mark-tile';
import { STEP, agentPair, agentPairs, clock, trips } from '@/lib/live';

type Props = {
  stem: string;
  endingPrefix?: string;
  endings: string[];
  between: [string, string, string];
  names: Partial<Record<MarkKind, string>>;
};

function Slot({ options, current, names }: { options: MarkKind[]; current: MarkKind; names: Props['names'] }) {
  const slot = useRef<HTMLSpanElement>(null);
  const [widths, setWidths] = useState<Partial<Record<MarkKind, number>>>({});

  useLayoutEffect(() => {
    const chips = Array.from(slot.current!.children);
    const measure = () => setWidths(Object.fromEntries(options.map((kind, i) => [kind, chips[i].getBoundingClientRect().width])));
    measure();
    const observer = new ResizeObserver(measure);
    chips.forEach((chip) => observer.observe(chip, { box: 'border-box' }));
    return () => observer.disconnect();
  }, [options]);

  return (
    <span className="swb-slot-frame">
      {options.map((kind) => (
        <span key={kind} className="swb-chip swb-slot-reserve" aria-hidden="true">{names[kind] ?? kind}</span>
      ))}
      <span ref={slot} className="swb-slot" style={{ inlineSize: widths[current] }}>
        {options.map((kind) => (
          <span key={kind} className={`swb-chip ${kind === current ? 'swb-chip-on' : ''}`} aria-hidden={kind !== current}>
            <MarkTile kind={kind} className="swb-chip-mark" />
            {names[kind] ?? kind}
          </span>
        ))}
      </span>
    </span>
  );
}

const starts = [...new Set(agentPairs.map(({ a }) => a))];
const ends = [...new Set(agentPairs.map(({ b }) => b))];

export function HeroTitle({ stem, endingPrefix, endings, between, names }: Props) {
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
  const pair = agentPair(step);
  return (
    <>
      <h1 id="landing-heading" className="text-display-xl swb-hero-title">
        <span className="block text-balance" data-hero-stem>
          {stem.endsWith('，') ? <>{stem.slice(0, -1)}<span className="swb-hero-comma">，</span></> : stem}
        </span>
        <span className="swb-rotate" aria-live="off">
          {endings.map((text, index) => (
            <span key={text} className={`swb-rotate-item${index === word ? ' swb-rotate-on' : ''}${endingPrefix ? ' whitespace-nowrap' : ''}`}
              aria-hidden={index !== word}>{`${endingPrefix ?? ''}${text}`}</span>
          ))}
        </span>
      </h1>
      <p className="swb-between mt-3 text-base" data-hero-pair={pair.n}>
        {between[0] && <span className="swb-between-text">{between[0]}</span>}
        <span className="swb-pair" data-with-suffix={between[2] ? '' : undefined}>
          <span className="swb-pair-lead">
            <Slot options={starts} current={pair.a} names={names} />
            <span className="swb-between-text">{between[1]}</span>
          </span>
          <span className="swb-pair-tail">
            <Slot options={ends} current={pair.b} names={names} />
            {between[2] && <span className="swb-between-text">{between[2]}</span>}
          </span>
        </span>
      </p>
    </>
  );
}
