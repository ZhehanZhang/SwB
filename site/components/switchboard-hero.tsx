'use client';

import Link from 'next/link';
import { useEffect, useLayoutEffect, useRef, type CSSProperties, type RefObject } from 'react';
import type { Locale } from '@/lib/i18n';
import { heroApps as nodes, PAIR_LEG_MS } from '@/lib/live';
import { getStartedHref } from '@/lib/setup';
import { useHeroMotion } from './hero-motion';
import { MarkTile } from './mark-tile';

const BEND = 0.14;
const layouts = {
  wide: { W: 1000, H: 820, r: 282, pulse: 5.5 },
  tall: { W: 1000, H: 1000, r: 338, pulse: 4.5 },
};
type Layout = keyof typeof layouts;

const angle = (i: number) => (i / nodes.length) * Math.PI * 2 - Math.PI / 2 + Math.PI / nodes.length;

function place(layout: Layout, i: number) {
  const { W, H, r } = layouts[layout];
  return { x: W / 2 + r * Math.cos(angle(i)), y: H / 2 + r * Math.sin(angle(i)) };
}

function cord(layout: Layout, i: number) {
  const { W, H } = layouts[layout];
  const { x, y } = place(layout, i);
  const dx = x - W / 2;
  const dy = y - H / 2;
  return { x, y, qx: W / 2 + dx / 2 - dy * BEND, qy: H / 2 + dy / 2 + dx * BEND };
}

function side(i: number) {
  const c = Math.cos(angle(i));
  const s = Math.sin(angle(i));
  if (Math.abs(c) < 0.35) return s < 0 ? 'top' : 'bottom';
  return c < 0 ? 'left' : 'right';
}

const pct = (value: number, total: number) => `${(value / total) * 100}%`;

function useCords(overlay: RefObject<SVGSVGElement | null>, layout: Layout, motion: ReturnType<typeof useHeroMotion>) {
  const snapshot = useRef(motion);
  const sync = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    snapshot.current = motion;
    sync.current?.();
  }, [motion]);

  useEffect(() => {
    const element = overlay.current;
    if (!element) return;
    const { W, H, pulse: radius } = layouts[layout];
    const curves = nodes.map((_, i) => cord(layout, i));
    const spokes = element.querySelectorAll<SVGPathElement>('.swb-transit');
    const pulse = element.querySelector<SVGCircleElement>('.swb-hub-pulse')!;
    let flowing = -1;
    let visible = false;
    const resize = () => {
      const { width } = element.getBoundingClientRect();
      if (width) pulse.setAttribute('r', String(radius * W / width));
    };

    const redraw = () => {
      const { pair, startedAt } = snapshot.current;
      const progress = visible && startedAt !== null ? (performance.now() - startedAt - 220) / PAIR_LEG_MS : 2;
      const moving = progress >= 0 && progress < 2;
      const entering = progress < 1;
      const spoke = moving ? nodes.findIndex(({ kind }) => kind === (entering ? pair.a : pair.b)) : -1;
      if (spoke !== flowing) {
        spokes[flowing]?.removeAttribute('data-flow');
        spokes[spoke]?.setAttribute('data-flow', '');
        flowing = spoke;
      }
      pulse.toggleAttribute('data-flow', moving);
      if (moving) {
        const t = entering ? 1 - progress : progress - 1;
        const u = 1 - t;
        const { x, y, qx, qy } = curves[spoke];
        pulse.setAttribute('cx', String(u * u * W / 2 + 2 * u * t * qx + t * t * x));
        pulse.setAttribute('cy', String(u * u * H / 2 + 2 * u * t * qy + t * t * y));
      }
      return progress < 2;
    };

    let frame = 0;
    const tick = () => {
      if (redraw()) frame = requestAnimationFrame(tick);
    };
    const update = () => {
      cancelAnimationFrame(frame);
      tick();
    };
    const viewWatch = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      update();
    });
    const sizeWatch = new ResizeObserver(resize);
    resize();
    sync.current = update;
    viewWatch.observe(element);
    sizeWatch.observe(element);
    return () => {
      sync.current = null;
      cancelAnimationFrame(frame);
      sizeWatch.disconnect();
      viewWatch.disconnect();
    };
  }, [overlay, layout]);
}

function Board({ layout, className, lang }: { layout: Layout; className: string; lang: Locale }) {
  const { W, H } = layouts[layout];
  const overlay = useRef<SVGSVGElement>(null);
  const motion = useHeroMotion();
  useCords(overlay, layout, motion);
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <svg ref={overlay} viewBox={`0 0 ${W} ${H}`} className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
        {nodes.map(({ kind }, i) => {
          const { x, y, qx, qy } = cord(layout, i);
          return <path key={kind} className="swb-transit" data-app={kind}
            data-active={motion.active && (kind === motion.pair.a || kind === motion.pair.b) ? '' : undefined}
            d={`M ${W / 2} ${H / 2} Q ${qx} ${qy} ${x} ${y}`} vectorEffect="non-scaling-stroke" />;
        })}
        <circle className="swb-hub-pulse" vectorEffect="non-scaling-stroke" />
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
