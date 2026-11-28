'use client';

import Link from 'next/link';
import { useEffect, useRef, type CSSProperties, type RefObject } from 'react';
import type { Locale } from '@/lib/i18n';
import { CYCLE, LEG, STEP, agentPair, clock, trips as livePairs } from '@/lib/live';
import { getStartedHref, type SetupMark } from '@/lib/setup';
import { MarkTile, type MarkKind } from './mark-tile';

type Node = { kind: SetupMark; label: string };

const nodes: Node[] = [
  { kind: 'slack', label: 'Slack' },
  { kind: 'claude', label: 'Claude' },
  { kind: 'capy', label: 'Capy Captain' },
  { kind: 'dots', label: 'Dots' },
  { kind: 'discord', label: 'Discord' },
  { kind: 'chatgpt', label: 'ChatGPT' },
  { kind: 'codex', label: 'Codex' },
  { kind: 'github', label: 'GitHub' },
  { kind: 'gemini', label: 'Gemini' },
  { kind: 'claude-code', label: 'Claude Code' },
  { kind: 'grok', label: 'Grok' },
  { kind: 'telegram', label: 'Telegram' },
  { kind: 'grok-bot', label: 'Grok Bot' },
  { kind: 'muse', label: 'Muse' },
];

const index = (kind: MarkKind) => nodes.findIndex((node) => node.kind === kind);
const trips = livePairs.map(([from, to]) => [index(from), index(to)] as const);
const firstPair = agentPair(0);

const RETURN_START = 2 * LEG + 0.1;

const layouts = {
  wide: { W: 1000, H: 820, r: 282, pulse: 5.5, initialWidth: 624 },
  tall: { W: 1000, H: 1000, r: 338, pulse: 4.5, initialWidth: 350 },
};
type Layout = keyof typeof layouts;
type Point = { x: number; y: number };

const angle = (i: number) => (i / nodes.length) * Math.PI * 2 - Math.PI / 2 + Math.PI / nodes.length;

function place(layout: Layout, i: number) {
  const { W, H, r } = layouts[layout];
  return { x: W / 2 + r * Math.cos(angle(i)), y: H / 2 + r * Math.sin(angle(i)) };
}

function cord(layout: Layout, i: number) {
  const { W, H, r } = layouts[layout];
  const start = { x: W / 2, y: H / 2 };
  const end = place(layout, i);
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const control = (along: number, bend: number) => ({
    x: start.x + along * dx - bend * dy,
    y: start.y + along * dy + bend * dx,
  });
  return { start, near: control(.3, .17), far: control(.64, .12), end, nx: -dy / r, ny: dx / r };
}

function ribbon({ start, near, far, end, nx, ny }: ReturnType<typeof cord>, scale: number) {
  const hub = 3.75 * scale;
  const app = .575 * scale;
  const edge = ({ x, y }: Point, offset: number, advance = 0) =>
    `${x + nx * offset + ny * advance} ${y + ny * offset - nx * advance}`;
  return [
    `M ${edge(start, hub)}`,
    `C ${edge(near, hub)} ${edge(far, app)} ${edge(end, app)}`,
    `C ${edge(end, app, 4 * app / 3)} ${edge(end, -app, 4 * app / 3)} ${edge(end, -app)}`,
    `C ${edge(far, -app)} ${edge(near, -hub)} ${edge(start, -hub)}`,
    `C ${edge(start, -hub, -4 * hub / 3)} ${edge(start, hub, -4 * hub / 3)} ${edge(start, hub)} Z`,
  ].join(' ');
}

function side(i: number) {
  const c = Math.cos(angle(i));
  const s = Math.sin(angle(i));
  if (Math.abs(c) < 0.35) return s < 0 ? 'top' : 'bottom';
  return c < 0 ? 'left' : 'right';
}

const pct = (value: number, total: number) => `${(value / total) * 100}%`;

function useCords(overlay: RefObject<SVGSVGElement | null>, layout: Layout) {
  useEffect(() => {
    const element = overlay.current;
    if (!element) return;
    const spec = layouts[layout];
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const curves = nodes.map((_, i) => cord(layout, i));
    const spokes = element.querySelectorAll<SVGPathElement>('.swb-transit');
    const pulses = element.querySelectorAll<SVGCircleElement>('.swb-hub-pulse');
    const flowing = new Set<number>();
    let activeStep = 0;
    const resize = () => {
      const { width } = element.getBoundingClientRect();
      if (!width) return;
      const scale = spec.W / width;
      spokes.forEach((path, i) => path.setAttribute('d', ribbon(curves[i], scale)));
      pulses.forEach((pulse) => pulse.setAttribute('r', String(spec.pulse * scale)));
    };

    const redraw = () => {
      const still = motion.matches;
      const time = still ? 0 : clock();
      const step = Math.floor(time / STEP) % trips.length;
      if (step !== activeStep) {
        activeStep = step;
        const { a, b } = agentPair(step);
        spokes.forEach((path, i) => path.toggleAttribute('data-active', nodes[i].kind === a || nodes[i].kind === b));
      }
      flowing.clear();
      trips.forEach(([from, to], i) => {
        const local = (time + CYCLE - i * STEP) % CYCLE;
        const returning = local >= RETURN_START;
        const progress = (returning ? local - RETURN_START : local) / LEG;
        const pulse = pulses[i];
        const moving = !still && progress < 2;
        pulse.toggleAttribute('data-flow', moving);
        if (!moving) return;

        const entering = progress < 1;
        const spoke = entering !== returning ? from : to;
        const t = entering ? 1 - progress : progress - 1;
        const u = 1 - t;
        const { start, near, far, end } = curves[spoke];
        const point = (axis: keyof Point) => (
          u ** 3 * start[axis] + 3 * u * u * t * near[axis] + 3 * u * t * t * far[axis] + t ** 3 * end[axis]
        );
        flowing.add(spoke);
        pulse.setAttribute('cx', String(point('x')));
        pulse.setAttribute('cy', String(point('y')));
      });
      spokes.forEach((path, i) => path.toggleAttribute('data-flow', flowing.has(i)));
    };

    let frame = 0;
    let visible = false;
    const tick = () => {
      redraw();
      frame = requestAnimationFrame(tick);
    };
    const update = () => {
      cancelAnimationFrame(frame);
      redraw();
      if (visible && !motion.matches) frame = requestAnimationFrame(tick);
    };
    const viewWatch = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    });
    const sizeWatch = new ResizeObserver(resize);
    resize();
    viewWatch.observe(element);
    sizeWatch.observe(element);
    motion.addEventListener('change', update);
    return () => {
      cancelAnimationFrame(frame);
      sizeWatch.disconnect();
      viewWatch.disconnect();
      motion.removeEventListener('change', update);
    };
  }, [overlay, layout]);
}

function Board({ layout, className, lang }: { layout: Layout; className: string; lang: Locale }) {
  const { W, H, initialWidth } = layouts[layout];
  const overlay = useRef<SVGSVGElement>(null);
  useCords(overlay, layout);
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <svg ref={overlay} viewBox={`0 0 ${W} ${H}`} className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
        {nodes.map(({ kind }, i) => (
          <path key={kind} className="swb-transit" data-app={kind}
            data-active={kind === firstPair.a || kind === firstPair.b ? '' : undefined}
            d={ribbon(cord(layout, i), W / initialWidth)} />
        ))}
        {livePairs.map(([from, to]) => (
          <circle key={`${from}-${to}`} className="swb-hub-pulse" vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <img className="swb-hub" src="/avatars/swb.png" alt="" />
      {nodes.map((node, i) => {
        const { x, y } = place(layout, i);
        return (
          <Link key={node.kind} href={getStartedHref(lang, [node.kind])} prefetch={false}
            className={`swb-node swb-node-${side(i)}${node.kind === 'claude' ? ' swb-node-invite' : ''}`}
            style={{ left: pct(x, W), top: pct(y, H), '--drift-delay': `-${i * 0.83}s`, '--drift-duration': `${8 + i % 4}s` } as CSSProperties}
            aria-label={node.label}>
            <MarkTile kind={node.kind} />
            <span className="swb-node-label">{node.label}</span>
          </Link>
        );
      })}
    </div>
  );
}

export function SwitchboardHero({ label, hint, lang }: { label: string; hint: string; lang: Locale }) {
  return (
    <figure className="swb-hero" aria-label={label}>
      <Board layout="wide" className="hidden sm:block" lang={lang} />
      <Board layout="tall" className="sm:hidden" lang={lang} />
      <figcaption className="text-center text-sm text-fd-muted-foreground">{hint}</figcaption>
    </figure>
  );
}
