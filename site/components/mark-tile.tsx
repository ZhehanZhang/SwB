import type { CSSProperties } from 'react';

export type MarkKind =
  | 'claude' | 'claude-code' | 'chatgpt' | 'codex' | 'gemini' | 'grok' | 'muse' | 'swb' | 'capy'
  | 'slack' | 'discord' | 'telegram' | 'github';

const avatar: Partial<Record<MarkKind, string>> = {
  claude: '/avatars/claude.png',
  'claude-code': '/avatars/claude-code.png',
  chatgpt: '/avatars/chatgpt.png',
  codex: '/avatars/codex.png',
  gemini: '/avatars/gemini.png',
  grok: '/avatars/grok.png',
  muse: '/avatars/muse.png',
  swb: '/avatars/swb.png',
  capy: '/marks/captain.webp',
};
const platform: Partial<Record<MarkKind, [string, string]>> = {
  slack: ['/marks/slack.svg', 'var(--logo-slack)'],
  github: ['/marks/github.svg', 'var(--foreground)'],
};
const platformColor: Partial<Record<MarkKind, string>> = {
  discord: '/marks/discord.svg',
  telegram: '/marks/telegram.svg',
};

export function MarkTile({ kind, className = '', style }: { kind: MarkKind; className?: string; style?: CSSProperties }) {
  const face = avatar[kind];
  if (face) {
    return <span className={`swb-mark-tile swb-mark-avatar ${className}`} style={style} aria-hidden="true"><img src={face} alt="" /></span>;
  }
  const masked = platform[kind];
  return (
    <span className={`swb-mark-tile ${className}`} style={style} aria-hidden="true">
      {masked
        ? <span className="swb-mark-glyph" style={{ backgroundColor: masked[1], maskImage: `url("${masked[0]}")`, WebkitMaskImage: `url("${masked[0]}")` }} />
        : <img className="swb-mark-glyph" src={platformColor[kind]} alt="" />}
    </span>
  );
}
