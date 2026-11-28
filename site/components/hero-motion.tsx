'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { createHeroSequence, PAIR_ACTIVE_MS, PAIR_RELEASE_MS, type HeroPair } from '@/lib/live';

type Frame = { pair: HeroPair; initial: HeroPair; active: boolean; startedAt: number | null };
const Motion = createContext<Frame | null>(null);

export function useHeroMotion() {
  const frame = useContext(Motion);
  if (!frame) throw new Error('Hero motion is missing');
  return frame;
}

export function HeroMotion({ initial, children }: { initial: HeroPair; children: ReactNode }) {
  const [frame, setFrame] = useState<Frame>({ pair: initial, initial, active: true, startedAt: null });

  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const next = createHeroSequence(Math.random, initial);
    let pair = next();
    let timer: number | undefined;
    const show = () => {
      setFrame({ pair, initial, active: true, startedAt: performance.now() });
      timer = window.setTimeout(() => {
        setFrame({ pair, initial, active: false, startedAt: null });
        timer = window.setTimeout(() => {
          pair = next();
          show();
        }, PAIR_RELEASE_MS);
      }, PAIR_ACTIVE_MS);
    };
    const update = () => {
      window.clearTimeout(timer);
      if (preference.matches) setFrame({ pair, initial, active: true, startedAt: null });
      else show();
    };
    update();
    preference.addEventListener('change', update);
    return () => {
      window.clearTimeout(timer);
      preference.removeEventListener('change', update);
    };
  }, [initial]);

  return <Motion.Provider value={frame}>{children}</Motion.Provider>;
}
