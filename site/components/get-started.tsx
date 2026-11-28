'use client';

import { useLayoutEffect, useState, type ReactNode } from 'react';
import {
  computerCommand, getStartedAgentIds, getStartedAgents, getStartedStyleId, museCompanionCommand,
  pipxCommand, readGetStartedSelection, runLocations, serverCommand,
  type GetStartedAgent, type RunLocation,
} from '@/lib/get-started';
import { getStartedCopy } from '@/lib/get-started-copy';
import { getStartedExplanation } from '@/lib/get-started-explanation';
import { localize, type Locale } from '@/lib/i18n';
import { personalizeSwb } from '@/lib/urls';
import { AppPicker } from './app-picker';
import { BlockHeader, GuideLink } from './block-actions';
import { ProviderMark } from './deploy-providers';
import { MarkTile } from './mark-tile';
import { CopyTextButton, PromptActions } from './prompt-actions';
import { SwbAddressField } from './swb-address-field';
import { useSwbAddress } from './swb-address-provider';
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
const action = 'swb-button swb-button-compact px-2 text-[13px] hover:bg-fd-muted max-[420px]:px-1.5 max-[420px]:text-xs';

function replaceLocation({ run, agents }: View, address: string | null) {
  const url = new URL(location.href);
  url.searchParams.delete('agents');
  url.searchParams.set('run', run);
  if (address) url.searchParams.set('url', address);
  else url.searchParams.delete('url');
  url.search = `agents=${agents.join(',')}&${url.searchParams}`;
  history.replaceState(history.state, '', url);
}

function Command({ text, labels, secondary }: { text: string; labels: { copyCommand: string; copied: string }; secondary?: ReactNode }) {
  return (
    <div className={styles.command}>
      <div className="swb-action-row px-2 py-1.5" data-command-actions>
        <CopyTextButton text={text} labels={{ copy: labels.copyCommand, copied: labels.copied }}
          className={action} />
        {secondary}
      </div>
      <pre tabIndex={0}><code>{text}</code></pre>
    </div>
  );
}

export function GetStarted({ lang, prompts, cloud }: { lang: Locale; prompts: Record<string, string>; cloud: ReactNode }) {
  const [view, setView] = useState(initialView);
  const { address } = useSwbAddress();
  const labels = getStartedCopy[lang];
  const explanations = getStartedExplanation[lang];
  const runLabels = { local: labels.runLocal, server: labels.runServer, cloud: labels.runCloud };
  const deployGuide = localize(lang, '/docs/deploy');

  useLayoutEffect(() => {
    function restore() {
      const { run, agents } = readGetStartedSelection(location.search);
      const order = [...agents, ...getStartedAgentIds.filter((id) => !agents.includes(id))];
      setView({ run, agents, order, ready: true, animate: false });
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
    replaceLocation(next, new URLSearchParams(location.search).has('url') ? address : null);
  }

  function toggleAgent(id: GetStartedAgent) {
    const picked = view.agents.includes(id);
    const agents = picked ? view.agents.filter((agent) => agent !== id) : [...view.agents, id];
    const order = picked ? view.order : [...agents, ...view.order.filter((agent) => !agents.includes(agent))];
    const next = { ...view, agents, order, animate: true };
    setView(next);
    replaceLocation(next, new URLSearchParams(location.search).has('url') ? address : null);
  }

  const tunnelLink = (
    <a className={action} href={`${deployGuide}#cloudflare-tunnel`}>
      <ProviderMark name="cloudflare" />{labels.reachAnywhere}
    </a>
  );

  return (
    <div className={`not-prose ${styles.page}`} data-get-started data-ready={view.ready} data-animate={view.animate}>
      <section className={styles.runSection} aria-labelledby="start-run">
        <BlockHeader guides={
          <>
            <span className={styles.runGuide} data-start-guide="local" data-active={view.run === 'local'}>
              <GuideLink href={localize(lang, '/docs/install')}>{labels.installGuide}</GuideLink>
            </span>
            <span className={styles.runGuide} data-start-guide="hosted" data-active={view.run !== 'local'}>
              <GuideLink href={deployGuide}>{labels.deployGuide}</GuideLink>
            </span>
          </>
        }>
          <h2 className={styles.title} id="start-run">{labels.runTitle}</h2>
        </BlockHeader>
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
              <Command text={computerCommand} labels={labels} secondary={tunnelLink} />
              <details className={styles.alternative}>
                <summary>{labels.pipx}</summary>
                <Command text={pipxCommand} labels={labels} />
              </details>
            </div>
          </div>
          <div className={styles.runPanel} data-start-run="server" data-active={view.run === 'server'} inert={view.run !== 'server'}>
            <div className={styles.runContent}>
              <Command text={serverCommand} labels={labels} secondary={tunnelLink} />
            </div>
          </div>
          <div className={styles.runPanel} data-start-run="cloud" data-active={view.run === 'cloud'} inert={view.run !== 'cloud'}>
            <div className={styles.runContent}>
              {cloud}
            </div>
          </div>
        </div>
        <div className={styles.address}>
          <label htmlFor="start-address">{labels.address}</label>
          <SwbAddressField id="start-address" label={labels.address} error={labels.addressError}
            onEdit={(next) => replaceLocation(view, next)} />
        </div>
      </section>
      <section aria-labelledby="start-agents">
        <h2 className={styles.title} id="start-agents">{labels.agentsTitle}</h2>
        <AppPicker mode="toggle" labelledBy="start-agents" apps={getStartedAgents}
          selected={view.ready ? view.agents : []} onChange={toggleAgent} />
        <div className={styles.agents}>
          {view.order.map((id) => {
            const { name, mark, prompt, open, docs } = agentsById.get(id)!;
            const active = view.agents.includes(id);
            const text = personalizeSwb(prompts[prompt], address);
            const companionHelp = id === 'muse' ? labels.museCompanionHelp
              : id === 'claude-code' || id === 'codex' ? labels.companionHelp : '';
            return (
              <div key={id} className={styles.agent} data-start-agent={id} data-active={active} inert={!active}>
                <section id={id} className={styles.agentBody} aria-labelledby={`${id}-heading`}>
                  <BlockHeader guides={
                    <>
                      {companionHelp && <GuideLink href={localize(lang, '/docs/companion')}>{labels.companionGuide}</GuideLink>}
                      <GuideLink href={localize(lang, docs)}>{labels.guide}</GuideLink>
                    </>
                  }>
                    <h3 className={styles.agentTitle} id={`${id}-heading`}>
                      <MarkTile kind={mark} className={styles.mark} />
                      {name}
                    </h3>
                  </BlockHeader>
                  {id === 'muse' && (
                    <>
                      <h4 className={styles.agentSubtitle}>{labels.muse.wake}</h4>
                      <p className={styles.procedure}>{labels.muse.beforeCommand}</p>
                      <Command text={personalizeSwb(museCompanionCommand, address)} labels={labels} />
                      <p className={styles.procedure}>{labels.muse.afterCommand}</p>
                      <h4 className={styles.agentSubtitle}>{labels.muse.tools}</h4>
                    </>
                  )}
                  <p className={styles.procedure} data-agent-before>
                    {explanations.before[id]}
                    {companionHelp && <> {companionHelp}</>}
                  </p>
                  <div className={styles.prompt}>
                    <PromptActions text={text} labels={labels} targets={open} />
                    <pre tabIndex={0} lang="en"><code>{text}</code></pre>
                  </div>
                  <p className={styles.procedure} data-agent-after>{explanations.after[id]}</p>
                  <p className={styles.procedure}>{labels.procedure[id]}</p>
                </section>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
