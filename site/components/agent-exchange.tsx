'use client';

import { Settings2, UserRound } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { MarkTile } from './mark-tile';
import { agentNames, exchangeCopy, exchangeScenes, type ExchangeScene } from '@/lib/exchange-copy';
import type { Locale } from '@/lib/i18n';
import { getStartedHref } from '@/lib/setup';

const MESSAGE_DELAY = 1250;
const HOLD_DELAY = 3300;
const START_STAGGER = 260;
const HOLD_STAGGER = 240;

function ExchangeWindow({ scene: { app, messages }, lang, index }: { scene: ExchangeScene; lang: Locale; index: number }) {
  const [shown, setShown] = useState(1);
  const history = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let timer: ReturnType<typeof setTimeout>;
    let count = 1;
    const play = () => {
      setShown(++count);
      timer = setTimeout(play, count % messages.length === 0 ? HOLD_DELAY + index * HOLD_STAGGER : MESSAGE_DELAY);
    };
    const update = () => {
      clearTimeout(timer);
      count = 1;
      setShown(1);
      if (!motion.matches) timer = setTimeout(play, MESSAGE_DELAY + index * START_STAGGER);
    };
    update();
    motion.addEventListener('change', update);
    return () => {
      clearTimeout(timer);
      motion.removeEventListener('change', update);
    };
  }, [index, messages.length]);

  useLayoutEffect(() => {
    const previous = history.current;
    const latest = previous?.nextElementSibling;
    if (!previous || !latest) return;
    const distance = latest.getBoundingClientRect().height + parseFloat(getComputedStyle(previous).rowGap);
    const animation = previous.animate(
      [{ transform: `translateY(${distance}px)` }, { transform: 'none' }],
      { duration: 250, easing: 'ease-out' },
    );
    return () => animation.cancel();
  }, [shown]);

  const name = (speaker: typeof messages[number][0]) => speaker === 'you' ? exchangeCopy[lang].you : agentNames[speaker];
  const renderMessage = ([speaker, text]: typeof messages[number], key: number, enter = false) => (
    <div key={key} className="swb-win-message flex shrink-0 items-start gap-2" data-new={enter ? '' : undefined}>
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
  );

  const start = Math.max(0, shown - messages.length * 2);
  const href = getStartedHref(lang, [app, ...messages.flatMap(([speaker]) => speaker === 'you' ? [] : [speaker])]);

  return (
    <div className="swb-win overflow-hidden rounded-lg border" data-exchange-window={app}>
      <div className="swb-win-bar flex items-center justify-between gap-2 border-b px-3">
        <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold">
          <MarkTile kind={app} className="swb-win-avatar" />
          <span className="truncate">{agentNames[app]}</span>
        </h3>
        <Link className="swb-win-setup swb-text-link inline-flex shrink-0 items-center gap-1.5 self-stretch whitespace-nowrap text-xs" href={href} prefetch={false}>
          <Settings2 aria-hidden="true" className="size-3.5" />
          {exchangeCopy[lang].setup}
        </Link>
      </div>
      <div className="relative min-w-0">
        <div className="swb-win-body swb-win-final" aria-hidden="true">
          {messages.map((message, i) => renderMessage(message, i))}
        </div>
        <div className="swb-win-body swb-win-live" aria-hidden="true">
          {shown > 1 && (
            <div className="swb-win-history" ref={history}>
              {Array.from({ length: shown - start - 1 }, (_, i) => renderMessage(messages[(start + i) % messages.length], start + i))}
            </div>
          )}
          {renderMessage(messages[(shown - 1) % messages.length], shown - 1, shown > 1)}
        </div>
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
