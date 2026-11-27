'use client';

import { Check } from 'lucide-react';
import { useLayoutEffect, useState, type ReactNode } from 'react';
import {
  computerCommand, getStartedAgentIds, getStartedAgents, getStartedStorageKey, getStartedStyleId, museCompanionCommand,
  personalizePrompt, pipxCommand, readGetStartedSelection, runLocations, serverCommand, validSwbOrigin,
  type GetStartedAgent, type RunLocation,
} from '@/lib/get-started';
import { getStartedCopy } from '@/lib/get-started-copy';
import { getStartedExplanation } from '@/lib/get-started-explanation';
import { localize, type Locale } from '@/lib/i18n';
import { ProviderMark } from './deploy-providers';
import { MarkTile } from './mark-tile';
import { CopyTextButton, PromptActions } from './prompt-actions';
import styles from './get-started.module.css';

type View = {
  run: RunLocation;
  agents: GetStartedAgent[];
  order: GetStartedAgent[];
  ready: boolean;
  animate: boolean;
};

const initialView: View = {
  run: 'local', agents: getStartedAgentIds, order: getStartedAgentIds, ready: false, animate: false,
};
const agentsById = new Map(getStartedAgents.map((agent) => [agent.id, agent]));

function writeAddress(address: string | null) {
  try {
    if (address) localStorage.setItem(getStartedStorageKey, address);
    else localStorage.removeItem(getStartedStorageKey);
  } catch {}
}

function readAddress() {
  const url = new URLSearchParams(location.search);
  if (url.has('url')) {
    const address = validSwbOrigin(url.get('url'));
    if (address) writeAddress(address);
    return address ?? '';
  }
  try {
    return validSwbOrigin(localStorage.getItem(getStartedStorageKey)) ?? '';
  } catch {
    return '';
  }
}

function replaceLocation({ run, agents }: View, address: string | null) {
  const url = new URL(location.href);
  url.searchParams.delete('agents');
  url.searchParams.set('run', run);
  if (address) url.searchParams.set('url', address);
  else url.searchParams.delete('url');
  url.search = `agents=${agents.join(',')}&${url.searchParams}`;
  history.replaceState(history.state, '', url);
}

function Command({ text, labels }: { text: string; labels: { copyCommand: string; copied: string } }) {
  return (
    <div className={styles.command} data-stacked={text.includes('\n') || text.length > 80}>
      <div className={styles.commandAction}>
        <CopyTextButton text={text} labels={{ copy: labels.copyCommand, copied: labels.copied }}
          className="swb-button swb-button-compact hover:bg-fd-muted" />
      </div>
      <pre tabIndex={0}><code>{text}</code></pre>
    </div>
  );
}

export function GetStarted({ lang, prompts, cloud }: { lang: Locale; prompts: Record<string, string>; cloud: ReactNode }) {
  const [view, setView] = useState(initialView);
  const [address, setAddress] = useState('');
  const [checkAddress, setCheckAddress] = useState(false);
  const labels = getStartedCopy[lang];
  const explanations = getStartedExplanation[lang];
  const runLabels = { local: labels.runLocal, server: labels.runServer, cloud: labels.runCloud };
  const origin = validSwbOrigin(address);
  const selfHosting = localize(lang, '/docs/self-hosting');
  const showAddressError = Boolean(checkAddress && address.trim() && !origin);

  useLayoutEffect(() => {
    function restore() {
      const { run, agents } = readGetStartedSelection(location.search);
      const order = [...agents, ...getStartedAgentIds.filter((id) => !agents.includes(id))];
      setView({ run, agents, order, ready: true, animate: false });
      setAddress(readAddress());
      return agents[0];
    }

    const first = restore();
    window.addEventListener('popstate', restore);
    let frame = 0;
    if (first && !location.hash) {
      frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => {
          document.getElementById(first)?.scrollIntoView({ block: 'start', behavior: 'instant' });
        });
      });
    }
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('popstate', restore);
    };
  }, []);

  useLayoutEffect(() => {
    if (!view.ready) return;
    const sheet = (document.getElementById(getStartedStyleId) as HTMLStyleElement | null)?.sheet;
    if (sheet) sheet.disabled = true;
  }, [view.ready]);

  function changeRun(run: RunLocation) {
    if (view.run === run) return;
    const next = { ...view, run, animate: true };
    setView(next);
    replaceLocation(next, origin);
  }

  function toggleAgent(id: GetStartedAgent) {
    const picked = view.agents.includes(id);
    const agents = picked ? view.agents.filter((agent) => agent !== id) : [...view.agents, id];
    const order = picked ? view.order : [...agents, ...view.order.filter((agent) => !agents.includes(agent))];
    const next = { ...view, agents, order, animate: true };
    setView(next);
    replaceLocation(next, origin);
  }

  const tunnelLink = (
    <a className={styles.supportLink} href={`${selfHosting}#cloudflare-tunnel`}>
      <ProviderMark name="cloudflare" />{labels.reachAnywhere}
    </a>
  );

  return (
    <div className={`not-prose ${styles.page}`} data-get-started data-ready={view.ready} data-animate={view.animate}>
      <section aria-labelledby="start-run">
        <h2 className={styles.title} id="start-run">{labels.runTitle}</h2>
        <p className={styles.runHelp} id="start-run-help">{labels.runHelp}</p>
        <div className={styles.segments} role="group" aria-labelledby="start-run" aria-describedby="start-run-help">
          {runLocations.map((run) => (
            <button key={run} type="button" aria-pressed={view.run === run} onClick={() => changeRun(run)}>
              {runLabels[run]}
            </button>
          ))}
        </div>
        <div className={styles.runPanels}>
          <div className={styles.runPanel} data-start-run="local" data-active={view.run === 'local'} inert={view.run !== 'local'}>
            <div className={styles.runContent}>
              <Command text={computerCommand} labels={labels} />
              <details className={styles.alternative}>
                <summary>{labels.pipx}</summary>
                <Command text={pipxCommand} labels={labels} />
              </details>
              <div className={styles.supportLinks}>
                {tunnelLink}
                <a className={styles.supportLink} href={localize(lang, '/docs/install')}>{labels.installGuide}</a>
              </div>
            </div>
          </div>
          <div className={styles.runPanel} data-start-run="server" data-active={view.run === 'server'} inert={view.run !== 'server'}>
            <div className={styles.runContent}>
              <Command text={serverCommand} labels={labels} />
              <div className={styles.supportLinks}>
                {tunnelLink}
                <a className={styles.supportLink} href={selfHosting}>{labels.selfHostingGuide}</a>
              </div>
            </div>
          </div>
          <div className={styles.runPanel} data-start-run="cloud" data-active={view.run === 'cloud'} inert={view.run !== 'cloud'}>
            <div className={styles.runContent}>
              {cloud}
              <a className={styles.supportLink} href={selfHosting}>{labels.selfHostingGuide}</a>
            </div>
          </div>
        </div>
        <div className={styles.address}>
          <label htmlFor="start-address">{labels.address}</label>
          <p className={styles.addressHelp} id="start-address-help">{labels.addressHelp}</p>
          <input id="start-address" type="url" value={address} placeholder="https://swb.example.com" autoComplete="url"
            spellCheck={false} aria-describedby="start-address-help" aria-invalid={showAddressError || undefined}
            aria-description={showAddressError ? labels.addressError : undefined}
            onChange={(event) => {
              const value = event.currentTarget.value;
              const valid = validSwbOrigin(value);
              setAddress(value);
              writeAddress(valid);
              replaceLocation(view, valid);
            }}
            onBlur={() => {
              setCheckAddress(true);
              if (origin) setAddress(origin);
            }}
          />
        </div>
      </section>
      <section aria-labelledby="start-agents">
        <h2 className={styles.title} id="start-agents">{labels.agentsTitle}</h2>
        <div className={styles.tiles} role="group" aria-labelledby="start-agents">
          {getStartedAgents.map(({ id, name, mark }) => (
            <button key={id} type="button" className={styles.tile} aria-pressed={view.ready && view.agents.includes(id)}
              aria-controls={id} onClick={() => toggleAgent(id)}>
              <MarkTile kind={mark} className={styles.mark} />
              <span>{name}</span>
              <Check className={styles.selectedMark} size={15} aria-hidden="true" />
            </button>
          ))}
        </div>
        <div className={styles.agents}>
          {view.order.map((id) => {
            const { name, mark, prompt, open, docs } = agentsById.get(id)!;
            const active = view.agents.includes(id);
            const text = personalizePrompt(prompts[prompt], origin);
            const companionHelp = id === 'muse' ? labels.museCompanionHelp
              : id === 'claude-code' || id === 'codex' ? labels.companionHelp : '';
            return (
              <div key={id} className={styles.agent} data-start-agent={id} data-active={active} inert={!active}>
                <section id={id} className={styles.agentBody} aria-labelledby={`${id}-heading`}>
                  <h3 className={styles.agentTitle} id={`${id}-heading`}>
                    <MarkTile kind={mark} className={styles.mark} />
                    {name}
                  </h3>
                  {id === 'muse' && (
                    <>
                      <h4 className={styles.agentSubtitle}>{labels.muse.wake}</h4>
                      <p className={styles.procedure}>{labels.muse.beforeCommand}</p>
                      <Command text={museCompanionCommand} labels={labels} />
                      <p className={styles.procedure}>{labels.muse.afterCommand}</p>
                      <h4 className={styles.agentSubtitle}>{labels.muse.tools}</h4>
                    </>
                  )}
                  <p className={styles.procedure} data-agent-before>
                    {explanations.before[id]}
                    {companionHelp && (
                      <> {companionHelp} <a className={styles.supportLink} href={localize(lang, '/docs/companion')}>
                        {labels.companionGuide}
                      </a></>
                    )}
                  </p>
                  <div className={styles.prompt}>
                    <PromptActions text={text} labels={labels} targets={open} />
                    <pre tabIndex={0} lang="en"><code>{text}</code></pre>
                  </div>
                  <p className={styles.procedure} data-agent-after>{explanations.after[id]}</p>
                  <p className={styles.procedure}>{labels.procedure[id]}</p>
                  <a className={styles.supportLink} href={localize(lang, docs)}>{labels.guide}</a>
                </section>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
