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

export function HeroTitle({ stem, endingPrefix, endings, between, names }: Props) {
  const [word, setWord] = useState(0);
  const [step, setStep] = useState(0);
  const subtitle = useRef<HTMLSpanElement>(null);
  const pair = agentPair(step);

  useLayoutEffect(() => {
    const slots = subtitle.current!.querySelectorAll<HTMLElement>('.swb-slot');
    const words = subtitle.current!.querySelectorAll<HTMLElement>('.swb-between-text');
    const chips = [...slots].map((slot) => slot.querySelector('.swb-chip-on')!);
    const resize = () => {
      const sameRow = slots[0].parentElement!.getBoundingClientRect().top === slots[1].parentElement!.getBoundingClientRect().top;
      let offset = 0;
      slots.forEach((slot, index) => {
        if (index && !sameRow) offset = 0;
        const style = getComputedStyle(slot);
        const border = parseFloat(style.borderInlineStartWidth) + parseFloat(style.borderInlineEndWidth);
        const base = parseFloat(style.width) + border;
        const active = parseFloat(getComputedStyle(chips[index]).width) + border;
        slot.style.setProperty('--swb-pill-offset', `${offset}px`);
        slot.style.setProperty('--swb-pill-scale', String(base ? active / base : 1));
        offset += active - base;
        words[index]?.style.setProperty('--swb-pill-offset', `${offset}px`);
      });
    };
    resize();
    const observer = new ResizeObserver(resize);
    [subtitle.current!, ...slots, ...chips].forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [pair.n]);

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
        <span ref={subtitle} className="swb-pair" data-with-suffix={between[2] ? '' : undefined}>
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
