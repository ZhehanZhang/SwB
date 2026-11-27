import { MarkTile, type MarkKind } from './mark-tile';

const W = 1100;
const H = 460;
const HUB = { x: 550, y: 230 };

const channels: { kind: MarkKind; label: string; y: number }[] = [
  { kind: 'slack', label: '#launch', y: 80 },
  { kind: 'discord', label: '#support', y: 180 },
  { kind: 'telegram', label: 'Release crew', y: 280 },
  { kind: 'github', label: 'acme/app', y: 380 },
];
const agents: { kind: MarkKind; label: string; y: number }[] = [
  { kind: 'claude', label: 'Claude Project', y: 55 },
  { kind: 'chatgpt', label: 'ChatGPT dot', y: 125 },
  { kind: 'capy', label: 'Capy Captain', y: 195 },
  { kind: 'grok', label: 'Grok routine', y: 265 },
  { kind: 'codex', label: 'Codex', y: 335 },
  { kind: 'claude-code', label: 'Claude Code', y: 405 },
];

const inPath = (y: number) => `M284 ${y} C410 ${y} 420 ${HUB.y} ${HUB.x - 34} ${HUB.y}`;
const outPath = (y: number) => `M${HUB.x + 34} ${HUB.y} C680 ${HUB.y} 690 ${y} 816 ${y}`;
const pct = (value: number, total: number) => `${(value / total) * 100}%`;

const trips = [
  { from: 0, to: 0, begin: 0 },
  { from: 1, to: 2, begin: 1.6 },
  { from: 3, to: 4, begin: 3.2 },
  { from: 2, to: 1, begin: 4.8 },
];

export function SwitchboardHero({ label }: { label: string }) {
  return (
    <figure className="swb-board" role="img" aria-label={label}>
      <svg className="absolute inset-0 size-full" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        {channels.map(({ y }, index) => (
          <path key={`in${index}`} id={`swb-in-${index}`} d={inPath(y)} className="swb-cord swb-cord-in" />
        ))}
        {agents.map(({ y }, index) => (
          <path key={`out${index}`} id={`swb-out-${index}`} d={outPath(y)} className="swb-cord swb-cord-out" />
        ))}
        {channels.map(({ y }, index) => <circle key={`ji${index}`} cx={284} cy={y} r={4} className="swb-jack swb-jack-in" />)}
        {agents.map(({ y }, index) => <circle key={`jo${index}`} cx={816} cy={y} r={4} className="swb-jack swb-jack-out" />)}
        <g className="swb-pulses">
          {trips.map(({ from, to, begin }) => (
            <g key={`${from}-${to}`}>
              <circle r={5} className="swb-pulse swb-pulse-in" visibility="hidden">
                <set attributeName="visibility" to="visible" begin={`${begin}s;${begin + 6.4}s`} dur="1.2s" />
                <animateMotion dur="1.2s" begin={`${begin}s;${begin + 6.4}s`} fill="remove" keyTimes="0;1" keyPoints="0;1" calcMode="linear">
                  <mpath href={`#swb-in-${from}`} />
                </animateMotion>
              </circle>
              <circle r={5} className="swb-pulse swb-pulse-out" visibility="hidden">
                <set attributeName="visibility" to="visible" begin={`${begin + 1.2}s;${begin + 7.6}s`} dur="1.2s" />
                <animateMotion dur="1.2s" begin={`${begin + 1.2}s;${begin + 7.6}s`} fill="remove" keyTimes="0;1" keyPoints="0;1" calcMode="linear">
                  <mpath href={`#swb-out-${to}`} />
                </animateMotion>
              </circle>
              <circle r={5} className="swb-pulse swb-pulse-back" visibility="hidden">
                <set attributeName="visibility" to="visible" begin={`${begin + 2.6}s;${begin + 9}s`} dur="1.2s" />
                <animateMotion dur="1.2s" begin={`${begin + 2.6}s;${begin + 9}s`} fill="remove" keyTimes="0;1" keyPoints="1;0" calcMode="linear">
                  <mpath href={`#swb-out-${to}`} />
                </animateMotion>
              </circle>
              <circle r={5} className="swb-pulse swb-pulse-back" visibility="hidden">
                <set attributeName="visibility" to="visible" begin={`${begin + 3.8}s;${begin + 10.2}s`} dur="1.2s" />
                <animateMotion dur="1.2s" begin={`${begin + 3.8}s;${begin + 10.2}s`} fill="remove" keyTimes="0;1" keyPoints="1;0" calcMode="linear">
                  <mpath href={`#swb-in-${from}`} />
                </animateMotion>
              </circle>
            </g>
          ))}
        </g>
      </svg>
      {channels.map(({ kind, label: name, y }) => (
        <div key={name} className="swb-node swb-node-in" style={{ top: pct(y, H), right: pct(W - 276, W) }}>
          <span className="swb-node-label">{name}</span>
          <MarkTile kind={kind} />
        </div>
      ))}
      <img className="swb-hub" src="/marks/swb.svg" alt="" style={{ left: pct(HUB.x, W), top: pct(HUB.y, H) }} />
      {agents.map(({ kind, label: name, y }) => (
        <div key={name} className="swb-node swb-node-out" style={{ top: pct(y, H), left: pct(824, W) }}>
          <MarkTile kind={kind} />
          <span className="swb-node-label">{name}</span>
        </div>
      ))}
    </figure>
  );
}
