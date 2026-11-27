'use client';

import { Check, Copy, ExternalLink, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import type { Locale } from '@/lib/i18n';
import { localize } from '@/lib/i18n';
import { openUrl, setup } from '@/lib/setup';
import { CYCLE, LEG, STEP, clock, trips as livePairs } from '@/lib/live';
import { MarkTile, type MarkKind } from './mark-tile';

type Node = { kind: MarkKind; label: string; channel?: boolean };

const nodes: Node[] = [
  { kind: 'slack', label: 'Slack', channel: true },
  { kind: 'claude', label: 'Claude' },
  { kind: 'capy', label: 'Captain' },
  { kind: 'dots', label: 'Dots' },
  { kind: 'discord', label: 'Discord', channel: true },
  { kind: 'chatgpt', label: 'ChatGPT' },
  { kind: 'codex', label: 'Codex' },
  { kind: 'github', label: 'GitHub', channel: true },
  { kind: 'gemini', label: 'Gemini' },
  { kind: 'claude-code', label: 'Claude Code' },
  { kind: 'grok', label: 'Grok' },
  { kind: 'telegram', label: 'Telegram', channel: true },
  { kind: 'grok-bot', label: 'Grok Bot' },
  { kind: 'muse', label: 'Muse' },
];

const index = (kind: MarkKind) => nodes.findIndex((node) => node.kind === kind);
const trips = livePairs.map(([from, to]) => [index(from), index(to)] as const);

const BEND = 0.14;

const layouts = {
  wide: { W: 1000, H: 820, r: 320, hub: 44, cord: [7, 2.4], pulse: 6, halo: 30 },
  tall: { W: 1000, H: 1000, r: 385, hub: 85, cord: [5, 1.8], pulse: 4.5, halo: 58 },
};
type Layout = keyof typeof layouts;

const angle = (i: number) => (i / nodes.length) * Math.PI * 2 - Math.PI / 2 + Math.PI / nodes.length;

function place(layout: Layout, i: number) {
  const { W, H, r } = layouts[layout];
  return { x: W / 2 + r * Math.cos(angle(i)), y: H / 2 + r * Math.sin(angle(i)) };
}

function side(i: number) {
  const c = Math.cos(angle(i));
  const s = Math.sin(angle(i));
  if (Math.abs(c) < 0.35) return s < 0 ? 'top' : 'bottom';
  return c < 0 ? 'left' : 'right';
}

const pct = (value: number, total: number) => `${(value / total) * 100}%`;
const smooth = (x: number) => x * x * (3 - 2 * x);

function useCords(canvas: React.RefObject<HTMLCanvasElement | null>, layout: Layout) {
  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const spec = layouts[layout];
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let colors = { intake: '#3b5bdb', route: '#2f7d4f', sink: '#c92a2a', accent: '#999', background: '#fff' };
    const readColors = () => {
      const style = getComputedStyle(element);
      const pick = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
      colors = { intake: pick('--cord-intake', colors.intake), route: pick('--cord-route', colors.route), sink: pick('--cord-sink', colors.sink), accent: pick('--accent', colors.accent), background: pick('--background', colors.background) };
    };
    readColors();
    const themeWatch = new MutationObserver(() => { readColors(); redraw(); });
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    let scale = 1;
    let redraw = () => {};
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const { width } = element.getBoundingClientRect();
      scale = width / spec.W;
      element.width = Math.round(width * ratio);
      element.height = Math.round((width * spec.H / spec.W) * ratio);
      context.setTransform(ratio * scale, 0, 0, ratio * scale, 0, 0);
      redraw();
    };
    resize();
    const sizeWatch = new ResizeObserver(resize);
    sizeWatch.observe(element);

    const cx = spec.W / 2;
    const cy = spec.H / 2;
    const curves = nodes.map((_, i) => {
      const { x, y } = place(layout, i);
      const dx = x - cx;
      const dy = y - cy;
      return { x, y, qx: cx + dx / 2 - dy * BEND, qy: cy + dy / 2 + dx * BEND };
    });
    const at = (i: number, t: number) => {
      const { x, y, qx, qy } = curves[i];
      const u = 1 - t;
      return {
        x: u * u * cx + 2 * u * t * qx + t * t * x,
        y: u * u * cy + 2 * u * t * qy + t * t * y,
        dx: 2 * u * (qx - cx) + 2 * t * (x - qx),
        dy: 2 * u * (qy - cy) + 2 * t * (y - qy),
      };
    };

    const legs = (time: number) => {
      const active: { cord: number; from: number; to: number; tone: 'intake' | 'route' | 'sink'; p: number }[] = [];
      const load = new Array(nodes.length).fill(0);
      const halos: { node: number; p: number }[] = [];
      trips.forEach(([a, b], n) => {
        const local = (time - n * STEP + CYCLE) % CYCLE;
        const span = LEG * 4 + 0.2;
        if (local < span + 0.6) {
          const lift = smooth(Math.min(1, local / 0.4)) * smooth(Math.min(1, Math.max(0, (span + 0.6 - local) / 0.6)));
          load[a] = Math.max(load[a], lift);
          load[b] = Math.max(load[b], lift);
        }
        const tone = (i: number): 'intake' | 'route' => (nodes[i].channel ? 'intake' : 'route');
        const plan = [
          { cord: a, from: 1, to: 0, tone: tone(a), start: 0 },
          { cord: b, from: 0, to: 1, tone: tone(b), start: LEG },
          { cord: b, from: 1, to: 0, tone: 'sink' as const, start: LEG * 2 + 0.1 },
          { cord: a, from: 0, to: 1, tone: 'sink' as const, start: LEG * 3 + 0.1 },
        ];
        plan.forEach(({ start, ...leg }) => {
          const p = (local - start) / LEG;
          if (p >= 0 && p <= 1) active.push({ ...leg, p });
        });
        if (local < 1.2) halos.push({ node: a, p: local / 1.2 });
        if (local > LEG * 2 && local < LEG * 2 + 1.2) halos.push({ node: b, p: (local - LEG * 2) / 1.2 });
      });
      return { active, load, halos };
    };

    const SAMPLES = 28;
    const draw = (time: number) => {
      context.clearRect(0, 0, spec.W, spec.H);
      const { active, load, halos } = still ? { active: [], load: new Array(nodes.length).fill(0), halos: [] } : legs(time);
      const grow = still ? 1 : smooth(Math.min(1, time / 1.2));
      const [hubWidth, nodeWidth] = spec.cord.map((width) => width / scale);
      nodes.forEach((node, i) => {
        const phase = i * 2.39;
        const breathe = still ? 0 : 0.1 * Math.sin(time * 0.7 + phase);
        const alpha = still ? 0.9 : 0.72 + 0.12 * Math.sin(time * 0.45 + phase * 1.3) + 0.16 * load[i];
        const swells = active.filter((leg) => leg.cord === i).map((leg) => leg.from + (leg.to - leg.from) * leg.p);
        const left: [number, number][] = [];
        const right: [number, number][] = [];
        for (let k = 0; k <= SAMPLES; k += 1) {
          const t = (k / SAMPLES) * grow;
          const { x, y, dx, dy } = at(i, t);
          const length = Math.hypot(dx, dy) || 1;
          const swell = swells.reduce((sum, s) => sum + 1.6 * Math.exp(-((t - s) ** 2) / 0.006), 0);
          const half = ((hubWidth + (nodeWidth - hubWidth) * t) * (1 + breathe + 0.45 * load[i]) + swell * hubWidth * 0.55) / 2;
          left.push([x - (dy / length) * half, y + (dx / length) * half]);
          right.push([x + (dy / length) * half, y - (dx / length) * half]);
        }
        context.beginPath();
        left.forEach(([x, y], k) => (k ? context.lineTo(x, y) : context.moveTo(x, y)));
        const end = left.length - 1;
        context.arc((left[end][0] + right[end][0]) / 2, (left[end][1] + right[end][1]) / 2, Math.hypot(left[end][0] - right[end][0], left[end][1] - right[end][1]) / 2, Math.atan2(left[end][1] - right[end][1], left[end][0] - right[end][0]), Math.atan2(right[end][1] - left[end][1], right[end][0] - left[end][0]), true);
        for (let k = right.length - 1; k >= 0; k -= 1) context.lineTo(right[k][0], right[k][1]);
        context.closePath();
        context.globalAlpha = alpha;
        context.fillStyle = node.channel ? colors.intake : colors.route;
        context.fill();
      });
      context.globalAlpha = 1;
      halos.forEach(({ node, p }) => {
        const { x, y } = place(layout, node);
        context.beginPath();
        context.arc(x, y, spec.halo + 14 * p, 0, Math.PI * 2);
        context.lineWidth = 2.5 / scale;
        context.strokeStyle = colors.accent;
        context.globalAlpha = 0.45 * (1 - p);
        context.stroke();
      });
      context.globalAlpha = 1;
      active.forEach(({ cord, from, to, tone, p }) => {
        const { x, y } = at(cord, from + (to - from) * p);
        context.beginPath();
        context.arc(x, y, spec.pulse / scale, 0, Math.PI * 2);
        context.fillStyle = colors[tone];
        context.fill();
        context.lineWidth = 2 / scale;
        context.strokeStyle = colors.background;
        context.stroke();
      });
    };

    if (still) {
      redraw = () => draw(0);
      draw(0);
      return () => { themeWatch.disconnect(); sizeWatch.disconnect(); };
    }
    let frame = 0;
    let visible = true;
    const tick = () => {
      if (visible) draw(clock());
      frame = requestAnimationFrame(tick);
    };
    const viewWatch = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
    viewWatch.observe(element);
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      themeWatch.disconnect();
      sizeWatch.disconnect();
      viewWatch.disconnect();
    };
  }, [canvas, layout]);
}

function Board({ layout, className, onPick }: { layout: Layout; className: string; onPick: (kind: MarkKind) => void }) {
  const { W, H } = layouts[layout];
  const canvas = useRef<HTMLCanvasElement>(null);
  useCords(canvas, layout);
  return (
    <div className={`swb-board swb-board-${layout} ${className}`} style={{ aspectRatio: `${W} / ${H}` }}>
      <canvas ref={canvas} className="swb-cords absolute inset-0 size-full" aria-hidden="true" />
      <span className="swb-hub-ring" style={{ left: '50%', top: '50%' }} aria-hidden="true" />
      <img className="swb-hub" src="/avatars/swb.png" alt="" style={{ left: '50%', top: '50%' }} />
      {nodes.map((node, i) => {
        const { x, y } = place(layout, i);
        return (
          <button key={node.kind} type="button" className={`swb-node swb-node-${side(i)}`} style={{ left: pct(x, W), top: pct(y, H), animationDelay: `${0.6 + i * 0.12}s` }}
            onClick={() => onPick(node.kind)} aria-label={node.label}>
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
        <a className="mt-5 inline-block" href="https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB" target="_blank" rel="noreferrer">
          <img src="https://render.com/images/deploy-to-render-button.svg" alt="Deploy to Render" width={153} height={40} />
        </a>
      </section>
    </div>
  );
}

export function SwitchboardHero({ label, lang, prompts, labels }: {
  label: string; lang: Locale; prompts: Record<string, string>; labels: Labels;
}) {
  const [picked, setPicked] = useState<MarkKind | null>(null);
  return (
    <figure className="swb-hero" aria-label={label}>
      <Board layout="wide" className="hidden sm:block" onPick={setPicked} />
      <Board layout="tall" className="sm:hidden" onPick={setPicked} />
      {picked && <Panel kind={picked} lang={lang} prompts={prompts} labels={labels} onClose={() => setPicked(null)} />}
    </figure>
  );
}
