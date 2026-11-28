import type { MarkKind } from '@/components/mark-tile';
import { localize, type Locale } from './i18n';

export type SetupMark = Exclude<MarkKind, 'swb'>;
export type Target = 'claude' | 'chatgpt' | 'grok';

export const openUrl: Record<Target, (prompt: string) => string> = {
  claude: (prompt) => `https://claude.ai/new?q=${encodeURIComponent(prompt)}`,
  chatgpt: (prompt) => `https://chatgpt.com/?q=${encodeURIComponent(prompt)}`,
  grok: (prompt) => `https://grok.com/?q=${encodeURIComponent(prompt)}`,
};

export const setup: Record<SetupMark, { open: Target[]; docs: string }> = {
  claude: { open: ['claude'], docs: '/docs/setup/claude-project' },
  chatgpt: { open: ['chatgpt'], docs: '/docs/setup/chatgpt' },
  dots: { open: ['chatgpt'], docs: '/docs/setup/dots' },
  capy: { open: ['claude', 'chatgpt'], docs: '/docs/setup/capy-captain' },
  grok: { open: ['grok'], docs: '/docs/setup/grok' },
  'grok-bot': { open: ['grok'], docs: '/docs/setup/grok-bot' },
  codex: { open: [], docs: '/docs/setup/coding-agent' },
  'claude-code': { open: [], docs: '/docs/setup/coding-agent' },
  gemini: { open: [], docs: '/docs/setup/coding-agent' },
  muse: { open: [], docs: '/docs/setup/muse' },
  slack: { open: ['claude', 'chatgpt'], docs: '/docs/setup/slack' },
  discord: { open: ['claude', 'chatgpt'], docs: '/docs/setup/discord' },
  telegram: { open: ['claude', 'chatgpt'], docs: '/docs/setup/telegram' },
  github: { open: ['claude', 'chatgpt'], docs: '/docs/setup/github' },
  webhook: { open: ['claude', 'chatgpt'], docs: '/docs/blocks/webhooks' },
};

export function getStartedHref(lang: Locale, marks: readonly SetupMark[] = []) {
  const path = localize(lang, '/docs/get-started');
  const agents = new Set(marks.map((mark) => mark === 'chatgpt' || mark === 'dots' ? 'chatgpt-dots' : mark));
  return agents.size ? `${path}?agents=${[...agents].join(',')}` : path;
}
