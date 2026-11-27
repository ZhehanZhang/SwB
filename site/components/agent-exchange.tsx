'use client';

import { useEffect, useState } from 'react';
import { MarkTile } from './mark-tile';
import type { Locale } from '@/lib/i18n';
import { agentNames, exchangeCopy, exchangeScenes } from '@/lib/exchange-copy';

const MESSAGE_DELAY = 1250;
const SCENE_DELAY = 3600;
const firstFrame = { scene: 0, shown: 1 };
const stillFrame = { scene: 0, shown: exchangeScenes[0].messages.length };

function usePlayback() {
  const [frame, setFrame] = useState(firstFrame);
  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let timer: ReturnType<typeof setTimeout>;
    let frame = firstFrame;
    const play = () => {
      const complete = frame.shown === exchangeScenes[frame.scene].messages.length;
      timer = setTimeout(() => {
        frame = complete
          ? { scene: (frame.scene + 1) % exchangeScenes.length, shown: 1 }
          : { scene: frame.scene, shown: frame.shown + 1 };
        setFrame(frame);
        play();
      }, complete ? SCENE_DELAY : MESSAGE_DELAY);
    };
    const update = () => {
      clearTimeout(timer);
      frame = motion.matches ? stillFrame : firstFrame;
      setFrame(frame);
      if (!motion.matches) play();
    };
    update();
    motion.addEventListener('change', update);
    return () => {
      clearTimeout(timer);
      motion.removeEventListener('change', update);
    };
  }, []);
  return frame;
}

export function AgentExchange({ lang }: { lang: Locale }) {
  const { scene, shown } = usePlayback();
  const { app, messages } = exchangeScenes[scene];
  const { you } = exchangeCopy[lang];

  return (
    <div className="swb-exchange mt-10">
      <div className="swb-win overflow-hidden rounded-xl border">
        <div className="swb-win-bar flex items-center gap-2 border-b px-4">
          <MarkTile kind={app} className="swb-win-mark" />
          <span className="text-sm font-semibold">{app === 'slack' ? 'Slack #launch' : agentNames[app]}</span>
        </div>
        <div key={app} className="swb-win-body flex min-h-0 flex-col-reverse overflow-y-auto overscroll-contain px-4 py-4 sm:px-5">
          <div className="shrink-0">
            {messages.slice(0, shown).map(([speaker, text], i) => (
              <div key={i} className="swb-win-message flex gap-3">
                {speaker === 'you'
                  ? <span className="swb-win-you grid size-8 shrink-0 place-items-center rounded-md text-sm font-semibold" aria-hidden="true">{you.slice(0, 1)}</span>
                  : speaker === 'capy'
                    ? <span className="swb-mark-tile swb-mark-avatar swb-win-avatar" aria-hidden="true"><img src="/avatars/capy-96.webp" alt="" /></span>
                    : <MarkTile kind={speaker} className="swb-win-avatar" />}
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-sm font-semibold">
                    {speaker === 'you' ? you : agentNames[speaker]}
                    {app === 'slack' && speaker !== 'you' && <img src="/avatars/swb.png" className="swb-win-bot" alt="SwB" title="SwB" />}
                  </p>
                  <p className="text-[15px] leading-6">{text[lang]}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
