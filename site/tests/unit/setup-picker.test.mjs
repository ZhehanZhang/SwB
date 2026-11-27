import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { combineSetupPrompts, setupSourceIds } from '../../lib/setup-picker.ts';

const sources = Object.fromEntries(setupSourceIds.map((id) =>
  [id, readFileSync(new URL(`../../../docs/prompts/${id}.md`, import.meta.url), 'utf8')],
));

function count(text, phrase) {
  return text.split(phrase).length - 1;
}

test('puts deploy first and orders choices independently of selection order', () => {
  const prompt = combineSetupPrompts(sources, ['telegram', 'claude-project', 'slack', 'telegram']);
  assert.equal(prompt, combineSetupPrompts(sources, ['claude-project', 'slack', 'telegram']));
  const sections = [...prompt.matchAll(/^### (.+)$/gm)].map(([, name]) => name);
  assert.deepEqual(sections, ['Claude Project', 'Slack', 'Telegram']);
  assert.ok(prompt.indexOf('Help me run SwB.') < prompt.indexOf('### Claude Project'));
  assert.equal(count(prompt, 'https://swb.one/prompts/coordinator.md'), 1);
  assert.equal(combineSetupPrompts(sources, []), '');
});

test('deduplicates shared steps while keeping each platform’s instructions', () => {
  const prompt = combineSetupPrompts(sources, ['chatgpt', 'dots', 'grok', 'codex', 'slack', 'discord', 'telegram', 'github']);
  for (const shared of [
    'Read the setup guide',
    'If the SwB tools are missing, ask me to add SwB as a custom MCP server.',
    'ask me to open https://chatgpt.com/plugins in a browser',
    'Keep tokens out of this chat',
    'Do each step marked "you" yourself.',
    'Give me each step marked "person" one at a time.',
    'switchboard.message.created',
  ]) assert.equal(count(prompt, shared), 1, shared);
  for (const specific of [
    'https://swb.one/docs/setup/chatgpt',
    'https://swb.one/docs/setup/dots',
    'Use the chatgpt mailbox for MCP events.',
    'Use the grok mailbox for MCP events.',
    'SwB checks your callback address before it sends events.',
    'Create New App > From a manifest',
    'Message Content Intent',
    'https://t.me/BotFather',
    'Developer settings > GitHub Apps > New GitHub App',
  ]) assert.ok(prompt.includes(specific), specific);
});

test('shows one coding-agent section with commands for each selected app', () => {
  const prompt = combineSetupPrompts(sources, ['codex', 'claude-code']);
  assert.equal(count(prompt, '### Claude Code, Codex'), 1);
  assert.equal(count(prompt, 'Call connect(what="coding-agent").'), 1);
  assert.equal(count(prompt, 'claude mcp add --transport http'), 1);
  assert.equal(count(prompt, 'codex mcp add switchboard'), 1);
  assert.equal(count(prompt, 'gemini mcp add --scope user'), 0);
});

test('uses the Muse OAuth connector and reads its mailbox in chat', () => {
  const prompt = combineSetupPrompts(sources, ['muse']);
  assert.match(prompt, /^### Muse$/m);
  assert.ok(prompt.includes('https://agent.meta.ai/api/hatch/oauth/callback'));
  assert.ok(prompt.includes('send(to="agent:muse"'));
  assert.ok(prompt.includes('At the start of each chat, call inbox'));
  assert.ok(!prompt.includes('switchboard.message.created'));
});
