'use client';

import { UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import { MarkTile } from './mark-tile';
import type { Locale } from '@/lib/i18n';
import { agentNames, exchangeCopy, exchangeScenes } from '@/lib/exchange-copy';

const MESSAGE_DELAY = 1250;
const SCENE_DELAY = 3600;
const TRANSCRIPT_LENGTH = 8;
const firstFrame = { scene: 0, shown: 1 };
const stillFrame = { scene: 0, shown: exchangeScenes[0].messages.length };
const transcript = exchangeScenes.flatMap(({ app, messages }, scene) =>
  messages.map(([speaker, text], index) => ({ app, scene, index, speaker, text })),
);
const loop = [...transcript, ...transcript];

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
  const { app } = exchangeScenes[scene];
  const { you } = exchangeCopy[lang];
  const end = transcript.length + 1 + transcript.findIndex((entry) => entry.scene === scene && entry.index === shown - 1);
  const entries = loop.slice(end - TRANSCRIPT_LENGTH, end);

  return (
    <div className="swb-exchange mt-10">
      <div className="swb-win overflow-hidden rounded-xl border" aria-hidden="true">
        <div className="swb-win-bar flex items-center gap-2 border-b px-4">
          <MarkTile kind={app} className="swb-win-avatar" />
          <span className="text-sm font-semibold">{app === 'slack' ? 'Slack #launch' : agentNames[app]}</span>
        </div>
        <div className="swb-win-body relative min-h-0 overflow-hidden">
          <div className="absolute inset-x-4 bottom-4 sm:inset-x-5">
            {entries.map(({ app: channel, scene: source, index, speaker, text }) => (
              <div key={`${source}/${index}`}
                className={`swb-win-entry flex gap-3${source === scene ? ' swb-win-message' : ''}${index === 0 ? ' swb-win-break' : ''}`}>
                {speaker === 'you'
                  ? <span className="swb-mark-tile swb-win-avatar swb-win-you" aria-hidden="true"><UserRound className="swb-mark-glyph" /></span>
                  : <MarkTile kind={speaker} className="swb-win-avatar" />}
                <div className="min-w-0">
                  <p className="text-sm font-semibold">
                    {speaker === 'you' ? you : agentNames[speaker]}
                    {speaker !== 'you' && speaker !== channel && <img src="/avatars/swb.png" className="swb-win-bot" alt="SwB" title="SwB" />}
                  </p>
                  <p className="text-[15px] leading-6">{text[lang]}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="sr-only">
        {exchangeScenes[0].messages.map(([speaker, text], index) => (
          <p key={index}><strong>{speaker === 'you' ? you : agentNames[speaker]}</strong> {text[lang]}</p>
        ))}
      </div>
    </div>
  );
}
