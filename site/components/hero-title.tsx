'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { heroApps, type HeroApp } from '@/lib/live';
import { useHeroMotion } from './hero-motion';
import { MarkTile } from './mark-tile';

type Props = {
  stem: string;
  endingPrefix?: string;
  endings: string[];
  between: [string, string, string];
};

const names = Object.fromEntries(heroApps.map(({ kind, label }) => [kind, kind === 'dots' ? 'ChatGPT dots' : label]));
const kinds = heroApps.map(({ kind }) => kind);

function Slot({ first, current }: { first: HeroApp; current: HeroApp }) {
  return (
    <span className="swb-slot">
      {[first, ...kinds.filter((kind) => kind !== first)].map((kind) => (
        <span key={kind} className={`swb-chip ${kind === current ? 'swb-chip-on' : ''}`} data-hero-app={kind} aria-hidden={kind !== current}>
          <MarkTile kind={kind} className="swb-chip-mark" />
          {names[kind]}
        </span>
      ))}
    </span>
  );
}

export function HeroTitle({ stem, endingPrefix, endings, between }: Props) {
  const [word, setWord] = useState(0);
  const subtitle = useRef<HTMLSpanElement>(null);
  const { pair, initial } = useHeroMotion();

  useLayoutEffect(() => {
    const row = subtitle.current!;
    const slots = row.querySelectorAll<HTMLElement>('.swb-slot');
    const chips = [...slots].map((slot) => slot.querySelector('.swb-chip-on')!);
    const resize = () => {
      const sameRow = slots[0].parentElement!.getBoundingClientRect().top === slots[1].parentElement!.getBoundingClientRect().top;
      row.toggleAttribute('data-shared-row', sameRow);
      slots.forEach((slot, index) => {
        const style = getComputedStyle(slot);
        const border = parseFloat(style.borderInlineStartWidth) + parseFloat(style.borderInlineEndWidth);
        const base = parseFloat(style.width) + border;
        const active = parseFloat(getComputedStyle(chips[index]).width) + border;
        const part = index === 0 ? 'lead' : 'tail';
        row.style.setProperty(`--swb-${part}-width`, `${base}px`);
        row.style.setProperty(`--swb-${part}-scale`, String(base ? active / base : 1));
      });
    };
    resize();
    const observer = new ResizeObserver(resize);
    [row, ...slots, ...chips].forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [pair.n]);

  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const epoch = performance.now();
    let timer: number | undefined;
    function onMotionChange() {
      window.clearInterval(timer);
      if (motion.matches) {
        setWord(0);
        return;
      }
      timer = window.setInterval(() => {
        setWord(Math.floor((performance.now() - epoch) / 2400) % endings.length);
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
        <span ref={subtitle} className="swb-pair">
          <span className="swb-pair-lead">
            <Slot first={initial.a} current={pair.a} />
            <span className="swb-between-text">{between[1]}</span>
          </span>
          <span className="swb-pair-tail">
            <Slot first={initial.b} current={pair.b} />
            {between[2] && <span className="swb-between-text">{between[2]}</span>}
          </span>
        </span>
      </p>
    </>
  );
}
