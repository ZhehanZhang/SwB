import type { MarkKind } from '@/components/mark-tile';

export const setupChoices = [
  { id: 'claude-project', prompt: 'claude-project', name: 'Claude Project', mark: 'claude' },
  { id: 'claude', prompt: 'claude', name: 'Claude', mark: 'claude' },
  { id: 'chatgpt', prompt: 'chatgpt', name: 'ChatGPT', mark: 'chatgpt' },
  { id: 'dots', prompt: 'dots', name: 'ChatGPT dots', mark: 'dots' },
  { id: 'muse', prompt: 'muse', name: 'Muse', mark: 'muse' },
  { id: 'grok', prompt: 'grok', name: 'Grok', mark: 'grok' },
  { id: 'grok-bot', prompt: 'grok-bot', name: 'Grok Bot', mark: 'grok-bot' },
  { id: 'capy-captain', prompt: 'capy-captain', name: 'Capy Captain', mark: 'capy' },
  { id: 'claude-code', prompt: 'coding-agent', name: 'Claude Code', mark: 'claude-code' },
  { id: 'codex', prompt: 'coding-agent', name: 'Codex', mark: 'codex' },
  { id: 'gemini-cli', prompt: 'coding-agent', name: 'Gemini CLI', mark: 'gemini' },
  { id: 'slack', prompt: 'slack', name: 'Slack', mark: 'slack' },
  { id: 'discord', prompt: 'discord', name: 'Discord', mark: 'discord' },
  { id: 'telegram', prompt: 'telegram', name: 'Telegram', mark: 'telegram' },
  { id: 'github', prompt: 'github', name: 'GitHub', mark: 'github' },
] as const satisfies readonly { id: string; prompt: string; name: string; mark: MarkKind }[];

type Choice = (typeof setupChoices)[number];
export type SetupChoice = Choice['id'];
export type SetupSources = Record<'deploy' | Choice['prompt'], string>;
export const setupSourceIds = [...new Set(['deploy', ...setupChoices.map(({ prompt }) => prompt)] as const)];

const guideLine = /^Read the setup guide at (https:\/\/\S+)\.\n+/;
const genericConnector = /^If the SwB tools are missing, ask me to add SwB as a custom MCP server\.[^\n]*(?:\n\nFor Claude,[^\n]*)?/m;
const chatgptConnector = /^Check your tools first\. If the SwB tools are missing, ask me to open https:\/\/chatgpt\.com\/plugins[^\n]*?Ask me to sign in to SwB as the owner\. ?/m;
const claimLine = /^Ask me to enter (?:a token only through the SwB claim link that connect returns|tokens only through the claim link that (?:connect|`connect`) returns|tokens through the claim links from `connect`)\. Keep tokens out of this chat(?: and tool arguments)?\. ?/m;
const agentStep = /Do each step marked "you" yourself\. ?/;
const personStep = /Give me each step marked "person" (?:one at a time with the exact click path(?: and links)?|one at a time\. Include the exact click path, config and links|separately\. Include its exact click path and links)\. Wait for me to say done after each step\. ?/;
const genericEvents = 'Subscribe to switchboard.message.created if this app supports MCP events. Otherwise, call wait or inbox to collect new messages.';
const mailboxEvents = /Subscribe to switchboard\.message\.created for the (\w+) mailbox if this app supports MCP events\. Otherwise, call wait or inbox at the start of each chat to collect new messages\./g;
const dotEvents = 'If your app supports MCP events, subscribe to switchboard.message.created for your SwB mailbox. Otherwise, call wait or inbox at the start of each task.';

export function combineSetupPrompts(sources: SetupSources, selection: Iterable<SetupChoice>) {
  const selected = new Set(selection);
  const choices = setupChoices.filter(({ id }) => selected.has(id));
  if (!choices.length) return '';

  const guides = new Set<string>();
  function read(id: keyof SetupSources) {
    return sources[id].trim().replace(guideLine, (_, url: string) => {
      guides.add(url);
      return '';
    });
  }

  const deploy = read('deploy');
  const shared = new Map<RegExp, string>();
  let events = false;
  const sections: string[] = [];
  const coding = choices.filter(({ prompt }) => prompt === 'coding-agent');

  for (const choice of choices) {
    const { prompt } = choice;
    if (prompt === 'coding-agent' && choice !== coding[0]) continue;
    let section = read(prompt);
    for (const pattern of [genericConnector, chatgptConnector, claimLine, agentStep, personStep]) {
      section = section.replace(pattern, (text) => {
        if (!shared.has(pattern)) shared.set(pattern, text.trim());
        return '';
      });
    }
    if (section.includes('switchboard.message.created')) {
      events = true;
      section = section
        .replace(genericEvents, '')
        .replace(mailboxEvents, 'Use the $1 mailbox for MCP events. When you use wait or inbox, check at the start of each chat.')
        .replace(dotEvents, 'Use your SwB mailbox for MCP events. When you use wait or inbox, check at the start of each task.');
    }

    const names = prompt === 'coding-agent' ? coding.map(({ name }) => name).join(', ') : choice.name;
    if (prompt === 'coding-agent') {
      section = section.split('\n\n').filter((paragraph) => !setupChoices.some(({ id, name, prompt }) =>
        prompt === 'coding-agent' && !selected.has(id) && paragraph.startsWith(`For ${name}, run `),
      )).join('\n\n')
        .replace('my coding agent', names)
        .replace('Ask me to name the agent I use.', 'Use the name of each selected coding agent in turn.');
    }
    sections.push(`### ${names}\n\n${section.replace(/\n{3,}/g, '\n\n').trim()}`);
  }

  const sharedSteps = [
    shared.get(genericConnector),
    shared.get(chatgptConnector) && `Use the following steps in ChatGPT. ${shared.get(chatgptConnector)}`,
    shared.get(claimLine) && 'Ask me to enter each token only through the claim link that connect returns. Keep tokens out of this chat and tool arguments.',
    [
      shared.get(agentStep),
      shared.get(personStep) && 'Give me each step marked "person" one at a time. Include each exact click path, link and config entry. Wait for me to say done after each step.',
    ].filter(Boolean).join(' '),
  ].filter(Boolean);
  return [
    `Read the setup guides at ${[...guides].join(', ')}.`,
    deploy,
    '## Connect my apps\n\nUse the steps for each selected app in order. Tell me which app to use when a step belongs in a different app.',
    ...sharedSteps,
    ...sections,
    ...(events ? [`## Events\n\n${genericEvents}`] : []),
  ].join('\n\n');
}
