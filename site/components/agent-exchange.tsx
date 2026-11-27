'use client';

import { useEffect, useState } from 'react';
import { MarkTile, type MarkKind } from './mark-tile';
import type { Locale } from '@/lib/i18n';
import { exchangeCopy, type Party } from '@/lib/exchange-copy';

const starters: Party[] = ['claude', 'grok-bot', 'capy', 'dots'];
const targets: Party[] = ['claude', 'grok-bot', 'capy', 'dots', 'muse'];
const app: Record<Party, string> = { claude: 'Claude', 'grok-bot': 'Grok Bot', capy: 'Capy', dots: 'ChatGPT', muse: 'Muse' };
const STEP = 900;

function usePlay(key: string, steps: number) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setStep(steps);
      return;
    }
    setStep(0);
    const timers = Array.from({ length: steps }, (_, i) => setTimeout(() => setStep(i + 1), STEP * (i + 1)));
    return () => timers.forEach(clearTimeout);
  }, [key, steps]);
  return step;
}

function Picker({ label, options, value, onChange, names }: {
  label: string; options: Party[]; value: Party; onChange: (party: Party) => void; names: Record<Party, string>;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className="mr-1 text-sm text-fd-muted-foreground">{label}</span>
      {options.map((party) => (
        <button key={party} type="button" aria-pressed={value === party} title={names[party]} aria-label={names[party]}
          className="swb-pick" onClick={() => onChange(party)}>
          <MarkTile kind={party as MarkKind} />
        </button>
      ))}
    </div>
  );
}

function Window({ party, title, children }: { party: MarkKind; title: string; children: React.ReactNode }) {
  return (
    <div className={`swb-win swb-win-${party} min-w-0 overflow-hidden rounded-xl border`}>
      <div className="swb-win-bar flex items-center gap-2 border-b px-3.5 py-2.5">
        <MarkTile kind={party} className="swb-win-mark" />
        <span className="text-sm font-semibold">{title}</span>
      </div>
      <div className="swb-win-body space-y-3 px-4 py-4">{children}</div>
    </div>
  );
}

function Bubble({ show, mine, children }: { show: boolean; mine?: boolean; children: React.ReactNode }) {
  return <p className={`swb-bubble ${mine ? 'swb-bubble-mine' : ''} ${show ? 'swb-bubble-in' : ''}`}>{children}</p>;
}

export function AgentExchange({ lang }: { lang: Locale }) {
  const copy = exchangeCopy[lang];
  const [mode, setMode] = useState<'agents' | 'slack'>('agents');
  const [from, setFrom] = useState<Party>('claude');
  const [to, setTo] = useState<Party>('grok-bot');
  const target = to === from ? targets.find((party) => party !== from)! : to;
  const step = usePlay(`${mode}-${from}-${target}`, mode === 'agents' ? 4 : 6);
  const task = copy.tasks[target];

  return (
    <div className="swb-exchange mt-10">
      <div className="mb-6 flex flex-wrap items-center gap-x-8 gap-y-4">
        <div className="swb-tabs inline-flex rounded-lg border p-1" role="tablist">
          {(['agents', 'slack'] as const).map((key) => (
            <button key={key} type="button" role="tab" aria-selected={mode === key} className="swb-tab" onClick={() => setMode(key)}>
              {copy.modes[key]}
            </button>
          ))}
        </div>
        {mode === 'agents' && (
          <>
            <Picker label={copy.from} options={starters} value={from} onChange={setFrom} names={copy.names} />
            <Picker label={copy.to} options={targets.filter((party) => party !== from)} value={target} onChange={setTo} names={copy.names} />
          </>
        )}
      </div>
      {mode === 'agents' ? (
        <div className="swb-exchange-grid">
          <Window party={from} title={app[from]}>
            <Bubble show mine>{copy.ask(copy.names[target], task.ask)}</Bubble>
            <Bubble show={step >= 1}>{copy.sent(copy.names[target])}</Bubble>
            <Bubble show={step >= 4}><b>{copy.names[target]}</b> {task.reply}</Bubble>
          </Window>
          <div className="swb-exchange-link" aria-hidden="true">
            <span className={`swb-exchange-pulse ${step >= 1 && step < 3 ? 'swb-go' : ''} ${step >= 3 && step < 4 ? 'swb-back' : ''}`} />
            <img src="/avatars/swb.png" alt="" className="swb-exchange-hub" />
          </div>
          <Window party={target} title={app[target]}>
            <Bubble show={step >= 2}><b>{copy.names[from]}</b> {task.ask}</Bubble>
            <Bubble show={step >= 3} mine>{task.reply}</Bubble>
          </Window>
        </div>
      ) : (
        <Window party="slack" title="#launch">
          {copy.slack.map(([who, text, name], i) => (
            <div key={i} className={`swb-slack-line flex gap-3 ${step >= i ? 'swb-bubble-in' : 'swb-bubble'}`}>
              {who === 'you'
                ? <span className="swb-you grid size-8 shrink-0 place-items-center rounded-md text-sm font-semibold" aria-hidden="true">{copy.you.slice(0, 1)}</span>
                : <MarkTile kind={who} className="swb-line-avatar" />}
              <div className="min-w-0">
                <p className="text-sm font-semibold">{who === 'you' ? copy.you : name ?? copy.names[who as Party]}</p>
                <p className="text-[15px] leading-relaxed">{text}</p>
              </div>
            </div>
          ))}
        </Window>
      )}
    </div>
  );
}
