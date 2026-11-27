import { MarkTile } from './mark-tile';

type Line = { who: 'you' | 'claude' | 'capy'; name: string; text: string };

export function Conversation({ title, channel, you, lines }: { title: string; channel: string; you: string; lines: Line[] }) {
  return (
    <section className="swb-convo py-12 sm:py-20">
      <h2 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">{title}</h2>
      <div className="mt-10 grid items-center gap-10 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-16">
        <div className="swb-thread rounded-xl border bg-fd-background">
          <div className="flex items-center gap-2.5 border-b px-5 py-3.5">
            <MarkTile kind="slack" />
            <span className="font-mono text-sm">{channel}</span>
          </div>
          <ol className="swb-thread-lines space-y-5 px-5 py-6" aria-label={title}>
            {lines.map((line, index) => (
              <li key={index} className="swb-line flex gap-3" style={{ animationDelay: `${index * 2.2}s` }}>
                {line.who === 'you'
                  ? <span className="swb-you grid size-9 shrink-0 place-items-center rounded-md font-semibold" aria-hidden="true">{you.slice(0, 1)}</span>
                  : <MarkTile kind={line.who} className="swb-line-avatar" />}
                <div className="min-w-0">
                  <p className="text-sm font-semibold">{line.name}</p>
                  <p className="mt-0.5 text-[15px] leading-relaxed">{line.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <svg className="swb-route mx-auto hidden w-full max-w-[300px] lg:block" viewBox="0 0 300 360" aria-hidden="true">
          <path id="swb-r1" d="M60 40 C60 120 150 100 150 180" className="swb-cord swb-cord-in" />
          <path id="swb-r2" d="M150 180 C150 260 60 240 60 320" className="swb-cord swb-cord-out" />
          <path id="swb-r3" d="M150 180 C150 260 240 240 240 320" className="swb-cord swb-cord-out" />
          {[['swb-r1', 0, 'swb-pulse-in', '0;1'], ['swb-r2', 1, 'swb-pulse-out', '0;1'], ['swb-r2', 2.2, 'swb-pulse-back', '1;0'], ['swb-r1', 3.2, 'swb-pulse-back', '1;0'],
            ['swb-r1', 4.4, 'swb-pulse-in', '0;1'], ['swb-r3', 5.4, 'swb-pulse-out', '0;1'], ['swb-r3', 6.6, 'swb-pulse-back', '1;0'], ['swb-r1', 7.6, 'swb-pulse-back', '1;0']]
            .map(([path, begin, cls, points], index) => (
              <circle key={index} r={6} className={`swb-pulse ${cls}`} visibility="hidden">
                <set attributeName="visibility" to="visible" begin={`${begin}s;${Number(begin) + 8.8}s`} dur="1s" />
                <animateMotion dur="1s" begin={`${begin}s;${Number(begin) + 8.8}s`} keyTimes="0;1" keyPoints={String(points)} calcMode="linear">
                  <mpath href={`#${path}`} />
                </animateMotion>
              </circle>
            ))}
          <foreignObject x="36" y="16" width="48" height="48"><MarkTile kind="slack" className="swb-route-node" /></foreignObject>
          <image href="/marks/swb.svg" x="122" y="152" width="56" height="56" />
          <foreignObject x="36" y="296" width="48" height="48"><MarkTile kind="claude" className="swb-route-node" /></foreignObject>
          <foreignObject x="216" y="296" width="48" height="48"><MarkTile kind="capy" className="swb-route-node" /></foreignObject>
        </svg>
      </div>
    </section>
  );
}
