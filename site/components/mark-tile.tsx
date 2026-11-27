import type { CSSProperties } from 'react';

export type MarkKind =
  | 'claude' | 'claude-code' | 'chatgpt' | 'codex' | 'gemini' | 'grok' | 'capy'
  | 'slack' | 'discord' | 'telegram' | 'github';

const color: Partial<Record<MarkKind, string>> = {
  claude: '/marks/claude.svg',
  'claude-code': '/marks/claude.svg',
  gemini: '/marks/gemini.svg',
  discord: '/marks/discord.svg',
  telegram: '/marks/telegram.svg',
};
const mono: Partial<Record<MarkKind, [string, string]>> = {
  chatgpt: ['/marks/openai.svg', 'var(--foreground)'],
  codex: ['/marks/openai.svg', '#fafafa'],
  github: ['/marks/github.svg', 'var(--foreground)'],
  grok: ['/marks/xai.svg', 'var(--foreground)'],
  slack: ['/marks/slack.svg', 'var(--logo-slack)'],
};

export function MarkTile({ kind, className = '', style }: { kind: MarkKind; className?: string; style?: CSSProperties }) {
  const dark = kind === 'claude-code' || kind === 'codex';
  const base = `swb-mark-tile ${dark ? 'swb-mark-tile-dark' : ''} ${className}`;
  if (kind === 'capy') {
    return <span className={`${base} swb-mark-tile-capy`} style={style} aria-hidden="true"><img src="/marks/captain.webp" alt="" /></span>;
  }
  const masked = mono[kind];
  return (
    <span className={base} style={style} aria-hidden="true">
      {masked
        ? <span className="swb-mark-glyph" style={{ backgroundColor: masked[1], maskImage: `url("${masked[0]}")`, WebkitMaskImage: `url("${masked[0]}")` }} />
        : <img className="swb-mark-glyph" src={color[kind]} alt="" />}
    </span>
  );
}
