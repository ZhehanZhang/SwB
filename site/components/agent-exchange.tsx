'use client';

import { UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import { MarkTile } from './mark-tile';
import { agentNames, exchangeCopy, exchangeScenes, type ExchangeScene } from '@/lib/exchange-copy';
import type { Locale } from '@/lib/i18n';

const MESSAGE_DELAY = 1250;
const HOLD_DELAY = 3600;
const STAGGER = 260;

function ExchangeWindow({ scene: { app, messages }, lang, index }: { scene: ExchangeScene; lang: Locale; index: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let timer: ReturnType<typeof setTimeout>;
    let count = 0;
    const play = () => {
      count = count === messages.length ? 1 : count + 1;
      setShown(count);
      timer = setTimeout(play, count === messages.length ? HOLD_DELAY : MESSAGE_DELAY);
    };
    const update = () => {
      clearTimeout(timer);
      count = 0;
      setShown(count);
      if (!motion.matches) timer = setTimeout(play, index * STAGGER);
    };
    update();
    motion.addEventListener('change', update);
    return () => {
      clearTimeout(timer);
      motion.removeEventListener('change', update);
    };
  }, [index, messages.length]);

  const name = (speaker: typeof messages[number][0]) => speaker === 'you' ? exchangeCopy[lang].you : agentNames[speaker];

  return (
    <div className="swb-win overflow-hidden rounded-lg border" data-exchange-window={app}>
      <h3 className="swb-win-bar flex items-center gap-2 border-b px-4 text-sm font-semibold">
        <MarkTile kind={app} className="swb-win-avatar" />
        {agentNames[app]}
      </h3>
      <div className="swb-win-body min-h-0 overflow-hidden px-4 py-3" aria-hidden="true">
        {messages.map(([speaker, text], i) => (
          <div key={i} className="swb-win-message flex items-start gap-2.5"
            data-pending={i >= shown ? '' : undefined} data-new={i === shown - 1 ? '' : undefined}>
            {speaker === 'you'
              ? <span className="swb-mark-tile swb-win-avatar swb-win-you"><UserRound className="swb-mark-glyph" /></span>
              : <MarkTile kind={speaker} className="swb-win-avatar" />}
            <div className="min-w-0">
              <p className="text-sm font-semibold">
                {name(speaker)}
                {speaker !== 'you' && speaker !== app && <img src="/avatars/swb.png" className="swb-win-bot" alt="SwB" title="SwB" />}
              </p>
              <p className="text-sm leading-5">{text[lang]}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="sr-only">
        {messages.map(([speaker, text], i) => <p key={i}><strong>{name(speaker)}</strong> {text[lang]}</p>)}
      </div>
    </div>
  );
}

export function AgentExchange({ lang }: { lang: Locale }) {
  return (
    <div className="swb-exchange mt-7">
      {exchangeScenes.map((scene, index) => <ExchangeWindow key={scene.app} scene={scene} lang={lang} index={index} />)}
    </div>
  );
}
