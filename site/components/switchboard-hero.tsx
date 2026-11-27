'use client';

import { BookOpen, Check, Copy, ExternalLink, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { Locale } from '@/lib/i18n';
import { localize } from '@/lib/i18n';
import { openUrl, setup } from '@/lib/setup';
import { CYCLE, LEG, STEP, agentPair, clock, trips as livePairs } from '@/lib/live';
import { MarkTile, type MarkKind } from './mark-tile';

type Node = { kind: MarkKind; label: string };

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
const smooth = (x: number) => x * x * (3 - 2 * x);

function useCords(canvas: React.RefObject<HTMLCanvasElement | null>, overlay: React.RefObject<SVGSVGElement | null>, layout: Layout) {
  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const spec = layouts[layout];
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let foreground = '#18181b';
    let background = '#fff';
    const readColors = () => {
      const style = getComputedStyle(element);
      foreground = style.getPropertyValue('--foreground').trim() || foreground;
      background = style.getPropertyValue('--background').trim() || background;
    };
    let scale = 1;
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const { width } = element.getBoundingClientRect();
      scale = width / spec.W;
      element.width = Math.round(width * ratio);
      element.height = Math.round((width * spec.H / spec.W) * ratio);
      context.setTransform(ratio * scale, 0, 0, ratio * scale, 0, 0);
      redraw();
    };

    const cx = spec.W / 2;
    const cy = spec.H / 2;
    const curves = nodes.map((_, i) => cord(layout, i));
    const spokes = overlay.current?.querySelectorAll('path');
    let activeStep = 0;
    const at = (i: number, t: number) => {
      const { x, y, qx, qy } = curves[i];
      const u = 1 - t;
      return {
        x: u * u * cx + 2 * u * t * qx + t * t * x,
        y: u * u * cy + 2 * u * t * qy + t * t * y,
      };
    };

    const transit = (time: number) => {
      const positions: { point: number; opacity: number }[][] = nodes.map(() => []);
      trips.forEach(([a, b], n) => {
        const local = (time + CYCLE - n * STEP) % CYCLE;
        const plan = [[a, 0, -1], [b, LEG, 1], [b, LEG * 2 + 0.1, -1], [a, LEG * 3 + 0.1, 1]];
        plan.forEach(([cord, start, direction]) => {
          const p = (local - start) / LEG;
          if (p >= 0 && p <= 1) {
            const opacity = 0.92 * smooth(Math.min(p / .16, (1 - p) / .16, 1));
            positions[cord].push({ point: direction === 1 ? p : 1 - p, opacity });
          }
        });
      });
      return positions;
    };

    const draw = (time: number, still: boolean) => {
      if (!scale) return;
      context.clearRect(0, 0, spec.W, spec.H);
      const positions = still ? [] : transit(time);
      const step = Math.floor(time / STEP) % trips.length;
      if (step !== activeStep) {
        activeStep = step;
        const { a, b } = agentPair(step);
        spokes?.forEach((path, i) => path.toggleAttribute('data-active', nodes[i].kind === a || nodes[i].kind === b));
      }
      spokes?.forEach((path, i) => path.toggleAttribute('data-flow', Boolean(positions[i]?.length)));
      if (still) return;
      context.fillStyle = foreground;
      context.strokeStyle = background;
      context.lineWidth = 1.5 / scale;
      positions.forEach((blobs, i) => blobs.forEach(({ point, opacity }) => {
        const { x, y } = at(i, point);
        context.globalAlpha = opacity;
        context.beginPath();
        context.arc(x, y, spec.pulse / scale, 0, Math.PI * 2);
        context.fill();
        context.stroke();
      }));
    };

    const redraw = () => draw(motion.matches ? 0 : clock(), motion.matches);
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
    const themeWatch = new MutationObserver(() => { readColors(); redraw(); });
    const sizeWatch = new ResizeObserver(resize);
    readColors();
    resize();
    viewWatch.observe(element);
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    sizeWatch.observe(element);
    motion.addEventListener('change', update);
    return () => {
      cancelAnimationFrame(frame);
      themeWatch.disconnect();
      sizeWatch.disconnect();
      viewWatch.disconnect();
      motion.removeEventListener('change', update);
    };
  }, [canvas, overlay, layout]);
}

function Board({ layout, className, onPick }: { layout: Layout; className: string; onPick: (kind: MarkKind) => void }) {
  const { W, H } = layouts[layout];
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<SVGSVGElement>(null);
  useCords(canvas, overlay, layout);
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <svg ref={overlay} viewBox={`0 0 ${W} ${H}`} className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
        {nodes.map(({ kind }, i) => {
          const { x, y, qx, qy } = cord(layout, i);
          return <path key={kind} className="swb-transit" data-app={kind}
            data-active={kind === firstPair.a || kind === firstPair.b ? '' : undefined}
            d={`M ${W / 2} ${H / 2} Q ${qx} ${qy} ${x} ${y}`} vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      <canvas ref={canvas} className="swb-cords absolute inset-0 size-full" aria-hidden="true" />
      <img className="swb-hub" src="/avatars/swb.png" alt="" />
      {nodes.map((node, i) => {
        const { x, y } = place(layout, i);
        return (
          <button key={node.kind} type="button" className={`swb-node swb-node-${side(i)}${node.kind === 'claude' ? ' swb-node-invite' : ''}`}
            style={{ left: pct(x, W), top: pct(y, H), '--drift-delay': `-${i * 0.83}s`, '--drift-duration': `${8 + i % 4}s` } as CSSProperties}
            onClick={() => onPick(node.kind)} aria-label={node.label} aria-haspopup="dialog">
            <MarkTile kind={node.kind} />
            <span className="swb-node-label">{node.label}</span>
          </button>
        );
      })}
    </div>
  );
}

type Labels = { copy: string; copied: string; open: string; guide: string; close: string };

function Panel({ kind, lang, prompts, labels, onClose }: {
  kind: MarkKind; lang: Locale; prompts: Record<string, string>; labels: Labels; onClose: () => void;
}) {
  const item = setup[kind];
  const node = nodes.find((entry) => entry.kind === kind);
  const [copied, setCopied] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  if (!item || !node) return null;
  const prompt = prompts[item.prompt] ?? '';
  const names = { claude: 'Claude', chatgpt: 'ChatGPT', grok: 'Grok' };
  return (
    <dialog ref={dialog} className="swb-panel" aria-label={node.label} onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect();
          if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) event.currentTarget.close();
        }
      }}>
      <div className="grid gap-4 p-5">
        <header className="flex items-center gap-3">
          <MarkTile kind={kind} className="swb-panel-mark" />
          <h2 className="font-display text-xl font-extrabold">{node.label}</h2>
          <form className="ml-auto" method="dialog">
            <button className="swb-button swb-button-icon hover:bg-fd-muted" aria-label={labels.close}>
              <X aria-hidden="true" />
            </button>
          </form>
        </header>
        <pre className="swb-panel-prompt">{prompt}</pre>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="swb-button swb-button-primary" onClick={async () => {
            await navigator.clipboard.writeText(prompt);
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
          }}>
            {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
            {labels.copy}
          </button>
          <span className="sr-only" role="status">{copied ? labels.copied : ''}</span>
          {item.open.map((target) => (
            <a key={target} className="swb-button swb-button-outline" href={openUrl[target](prompt)} target="_blank" rel="noreferrer">
              <ExternalLink aria-hidden="true" />{labels.open.replace('{app}', names[target])}
            </a>
          ))}
          <Link className="swb-button hover:bg-fd-muted" href={localize(lang, item.docs)}><BookOpen aria-hidden="true" />{labels.guide}</Link>
        </div>
      </div>
    </dialog>
  );
}

export function SwitchboardHero({ label, hint, lang, prompts, labels }: {
  label: string; hint: string; lang: Locale; prompts: Record<string, string>; labels: Labels;
}) {
  const [picked, setPicked] = useState<MarkKind | null>(null);
  return (
    <figure className="swb-hero" aria-label={label}>
      <Board layout="wide" className="hidden sm:block" onPick={setPicked} />
      <Board layout="tall" className="sm:hidden" onPick={setPicked} />
      <figcaption className="text-center text-sm text-fd-muted-foreground">{hint}</figcaption>
      {picked && <Panel kind={picked} lang={lang} prompts={prompts} labels={labels} onClose={() => setPicked(null)} />}
    </figure>
  );
}
