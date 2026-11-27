import { MarkTile, type MarkKind } from './mark-tile';

type Node = { kind: MarkKind; label: string; channel?: boolean };

const nodes: Node[] = [
  { kind: 'slack', label: 'Slack', channel: true },
  { kind: 'claude', label: 'Claude' },
  { kind: 'capy', label: 'Captain' },
  { kind: 'discord', label: 'Discord', channel: true },
  { kind: 'chatgpt', label: 'ChatGPT' },
  { kind: 'codex', label: 'Codex' },
  { kind: 'github', label: 'GitHub', channel: true },
  { kind: 'gemini', label: 'Gemini' },
  { kind: 'claude-code', label: 'Claude Code' },
  { kind: 'telegram', label: 'Telegram', channel: true },
  { kind: 'grok', label: 'Grok' },
  { kind: 'muse', label: 'Muse' },
];

const index = (kind: MarkKind) => nodes.findIndex((node) => node.kind === kind);
const trips: [MarkKind, MarkKind][] = [
  ['slack', 'claude'],
  ['claude', 'capy'],
  ['chatgpt', 'codex'],
  ['github', 'claude-code'],
  ['capy', 'telegram'],
  ['gemini', 'muse'],
  ['discord', 'grok'],
];
const LEG = 0.8;
const STEP = 2.1;
const CYCLE = trips.length * STEP;

const layouts = {
  wide: { W: 1000, H: 560, rx: 340, ry: 215 },
  tall: { W: 1000, H: 1000, rx: 380, ry: 380 },
};
type Layout = keyof typeof layouts;

function place(layout: Layout, i: number) {
  const { W, H, rx, ry } = layouts[layout];
  const angle = ((i + 0.5) / nodes.length) * Math.PI * 2 - Math.PI / 2;
  return { x: W / 2 + rx * Math.cos(angle), y: H / 2 + ry * Math.sin(angle) };
}

function cord(layout: Layout, i: number) {
  const { W, H } = layouts[layout];
  const { x, y } = place(layout, i);
  const cx = W / 2;
  const cy = H / 2;
  const mx = (x + cx) / 2 - (y - cy) * 0.12;
  const my = (y + cy) / 2 + (x - cx) * 0.12;
  return `M${x.toFixed(1)} ${y.toFixed(1)} Q${mx.toFixed(1)} ${my.toFixed(1)} ${cx} ${cy}`;
}

const pct = (value: number, total: number) => `${(value / total) * 100}%`;
const times = (start: number) => `${start}s;${(start + CYCLE).toFixed(1)}s`;

function Pulse({ path, start, tone, back }: { path: string; start: number; tone: string; back?: boolean }) {
  return (
    <circle r={6} className={`swb-pulse ${tone}`} visibility="hidden">
      <set attributeName="visibility" to="visible" begin={times(start)} dur={`${LEG}s`} />
      <animateMotion dur={`${LEG}s`} begin={times(start)} keyTimes="0;1" keyPoints={back ? '1;0' : '0;1'} calcMode="linear">
        <mpath href={`#${path}`} />
      </animateMotion>
    </circle>
  );
}

function Board({ layout, className }: { layout: Layout; className: string }) {
  const { W, H } = layouts[layout];
  const id = (i: number) => `swb-${layout}-${i}`;
  const tone = (i: number) => (nodes[i].channel ? 'swb-pulse-in' : 'swb-pulse-out');
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <svg className="absolute inset-0 size-full" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        {nodes.map((node, i) => (
          <path key={i} id={id(i)} d={cord(layout, i)} pathLength={1}
            className={`swb-cord ${node.channel ? 'swb-cord-in' : 'swb-cord-out'}`} style={{ animationDelay: `${i * 0.06}s` }} />
        ))}
        <g className="swb-pulses">
          {trips.map(([from, to], n) => [[from, n * STEP], [to, n * STEP + LEG * 2]] as const).flat().map(([kind, at], k) => {
            const { x, y } = place(layout, index(kind));
            return (
              <circle key={`h${k}`} cx={x} cy={y} r={layout === 'wide' ? 34 : 64} className="swb-halo" opacity={0}>
                <animate attributeName="opacity" values="0;0.5;0" dur="1.2s" begin={times(at)} />
              </circle>
            );
          })}
          {trips.map(([from, to], n) => {
            const a = index(from);
            const b = index(to);
            const start = n * STEP;
            return (
              <g key={n}>
                <Pulse path={id(a)} start={start} tone={tone(a)} />
                <Pulse path={id(b)} start={start + LEG} tone={tone(b)} back />
                <Pulse path={id(b)} start={start + LEG * 2 + 0.1} tone="swb-pulse-back" />
                <Pulse path={id(a)} start={start + LEG * 3 + 0.1} tone="swb-pulse-back" back />
              </g>
            );
          })}
        </g>
      </svg>
      <span className="swb-hub-ring" style={{ left: '50%', top: '50%' }} aria-hidden="true" />
      <img className="swb-hub" src="/avatars/swb.png" alt="" style={{ left: '50%', top: '50%' }} />
      {nodes.map((node, i) => {
        const { x, y } = place(layout, i);
        const side = x < W / 2 ? 'swb-node-left' : 'swb-node-right';
        return (
          <div key={node.kind} className={`swb-node ${side}`} style={{ left: pct(x, W), top: pct(y, H) }}>
            <MarkTile kind={node.kind} />
            <span className="swb-node-label">{node.label}</span>
          </div>
        );
      })}
    </div>
  );
}

export function SwitchboardHero({ label }: { label: string }) {
  return (
    <figure className="swb-hero" role="img" aria-label={label}>
      <Board layout="wide" className="hidden sm:block" />
      <Board layout="tall" className="sm:hidden" />
    </figure>
  );
}
