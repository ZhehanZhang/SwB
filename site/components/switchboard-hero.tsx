'use client';

import { Check, Copy, ExternalLink, X } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import type { Locale } from '@/lib/i18n';
import { localize } from '@/lib/i18n';
import { openUrl, setup } from '@/lib/setup';
import { CYCLE, LEG, STEP, clock, trips as livePairs } from '@/lib/live';
import { DEPLOY_BUTTONS_LIVE } from '@/lib/flags';
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
    const highlights = overlay.current?.querySelectorAll('path');
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
      highlights?.forEach((path, i) => path.toggleAttribute('data-flow', Boolean(positions[i]?.length)));
      context.strokeStyle = foreground;
      context.fillStyle = foreground;
      context.lineWidth = 1.6 / scale;
      curves.forEach(({ x, y, qx, qy }, i) => {
        const fade = still ? 0.2 : 0.3 * smooth(Math.max(0, Math.sin(time * 0.72 + i * 2.39)));
        if (!fade) return;
        context.globalAlpha = fade;
        context.beginPath();
        context.moveTo(cx, cy);
        context.quadraticCurveTo(qx, qy, x, y);
        context.stroke();
      });
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

function Board({ layout, className, onPick }: { layout: Layout; className: string; onPick: (kind: MarkKind, trigger: HTMLButtonElement) => void }) {
  const { W, H } = layouts[layout];
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<SVGSVGElement>(null);
  useCords(canvas, overlay, layout);
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <svg ref={overlay} viewBox={`0 0 ${W} ${H}`} className="pointer-events-none absolute inset-0 size-full" aria-hidden="true">
        {nodes.map(({ kind }, i) => {
          const { x, y, qx, qy } = cord(layout, i);
          return <path key={kind} className="swb-transit" d={`M ${W / 2} ${H / 2} Q ${qx} ${qy} ${x} ${y}`} vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      <canvas ref={canvas} className="swb-cords absolute inset-0 size-full" aria-hidden="true" />
      <img className="swb-hub" src="/avatars/swb.png" alt="" />
      {nodes.map((node, i) => {
        const { x, y } = place(layout, i);
        return (
          <button key={node.kind} type="button" className={`swb-node swb-node-${side(i)}${node.kind === 'claude' ? ' swb-node-invite' : ''}`}
            style={{ left: pct(x, W), top: pct(y, H), '--drift-delay': `-${i * 0.83}s`, '--drift-duration': `${8 + i % 4}s` } as CSSProperties}
            onClick={(event) => onPick(node.kind, event.currentTarget)} aria-label={node.label} aria-haspopup="dialog">
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
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const key = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [onClose]);
  if (!item || !node) return null;
  const prompt = prompts[item.prompt] ?? '';
  const names = { claude: 'Claude', chatgpt: 'ChatGPT', grok: 'Grok' };
  return (
    <div className="swb-panel-wrap" onClick={onClose}>
      <section className="swb-panel" role="dialog" aria-modal="true" aria-label={node.label} onClick={(event) => event.stopPropagation()}>
        <header className="flex items-center gap-3">
          <MarkTile kind={kind} className="swb-panel-mark" />
          <h2 className="font-display text-xl font-extrabold">{node.label}</h2>
          <button ref={close} type="button" className="ml-auto grid size-9 place-items-center rounded-md hover:bg-fd-muted" aria-label={labels.close} onClick={onClose}>
            <X aria-hidden="true" className="size-4" />
          </button>
        </header>
        <p className="mt-4 text-[15px]">{item.step[lang]}</p>
        <pre className="swb-panel-prompt">{prompt}</pre>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className="swb-button bg-fd-primary text-fd-primary-foreground hover:bg-fd-primary/85" onClick={async () => {
            await navigator.clipboard.writeText(prompt);
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
          }}>
            {copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
            {copied ? labels.copied : labels.copy}
          </button>
          {item.open.map((target) => (
            <a key={target} className="swb-button border hover:bg-fd-muted" href={openUrl[target](prompt)} target="_blank" rel="noreferrer">
              {labels.open.replace('{app}', names[target])}<ExternalLink aria-hidden="true" className="size-3.5" />
            </a>
          ))}
          <Link className="swb-button hover:bg-fd-muted" href={localize(lang, item.docs)}>{labels.guide}</Link>
        </div>
        {DEPLOY_BUTTONS_LIVE && (
          <a className="mt-5 inline-block" href="https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB" target="_blank" rel="noreferrer">
            <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" width={153} height={40} />
          </a>
        )}
      </section>
    </div>
  );
}

export function SwitchboardHero({ label, lang, prompts, labels }: {
  label: string; lang: Locale; prompts: Record<string, string>; labels: Labels;
}) {
  const [picked, setPicked] = useState<MarkKind | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pick = (kind: MarkKind, button: HTMLButtonElement) => {
    trigger.current = button;
    setPicked(kind);
  };
  const close = useCallback(() => {
    setPicked(null);
    trigger.current?.focus();
  }, []);
  return (
    <figure className="swb-hero" aria-label={label}>
      <Board layout="wide" className="hidden sm:block" onPick={pick} />
      <Board layout="tall" className="sm:hidden" onPick={pick} />
      {picked && <Panel kind={picked} lang={lang} prompts={prompts} labels={labels} onClose={close} />}
    </figure>
  );
}
